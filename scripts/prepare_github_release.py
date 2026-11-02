"""Build an isolated signed-update candidate. Does not change version, install or upload.

Run with the project Python. Launch in the background and watch status.json/build.log.
The Sparkle distribution must be downloaded from its official release and verified
against the hash pinned in packaging/update-config.json.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", NS)


def run(command, **kwargs):
    print("RUN", command[0], flush=True)
    return subprocess.run([str(c) for c in command], check=True, **kwargs)


def build(out: Path, sparkle: Path, resume: bool = False):
    out.mkdir(parents=True, exist_ok=resume)
    status = out / "status.json"
    def checkpoint(state, **data):
        temp = status.with_suffix('.tmp')
        temp.write_text(json.dumps({"state": state, "updated_at": datetime.now(timezone.utc).isoformat(), **data}, indent=2)+"\n")
        temp.replace(status)
    try:
        checkpoint("building")
        config = json.loads((ROOT / "packaging/update-config.json").read_text())
        dependency_archive = sparkle.parent / f"Sparkle-{config['sparkle_version']}.tar.xz"
        if not dependency_archive.is_file() or hashlib.sha256(dependency_archive.read_bytes()).hexdigest() != config['sparkle_sha256']:
            raise RuntimeError("Sparkle archive does not match the pinned official SHA-256")
        run(["codesign", "--verify", "--deep", "--strict", sparkle / "Sparkle.framework"])
        spec = importlib.util.spec_from_file_location("bv_bundle_builder", ROOT / "scripts/build_macos.py")
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        # Independent output tree; preserve the installed/dist application.
        builder.BUILD, builder.DIST = out / "build", out / "dist"
        builder.BUILD.mkdir(parents=True, exist_ok=resume)
        if not resume and builder.run_worker(False):
            raise RuntimeError("PyInstaller candidate build failed")
        app = builder.DIST / "BackyardVisitor.app"
        framework_dir = app / "Contents/Frameworks"
        distribution_info = plistlib.loads((sparkle / "Sparkle.framework/Resources/Info.plist").read_bytes())
        if distribution_info["CFBundleShortVersionString"] != config["sparkle_version"]:
            raise RuntimeError("Wrong Sparkle distribution version")
        if not (framework_dir / "Sparkle.framework").exists():
            shutil.copytree(sparkle / "Sparkle.framework", framework_dir / "Sparkle.framework", symlinks=True)
        run(["xcrun", "clang", "-dynamiclib", "-fobjc-arc", "-mmacosx-version-min=12.0", "-framework", "Cocoa", "-framework", "Sparkle", "-F"+str(sparkle), "-Wl,-rpath,@loader_path", "-Wl,-install_name,@rpath/libBackyardUpdater.dylib", ROOT / "native/updater_bridge.m", "-o", framework_dir / "libBackyardUpdater.dylib"])
        key_file = ROOT / ".release-secrets/sparkle-ed25519.key"
        public = run(["xcrun", "swift", "-module-cache-path", out / "swift-cache", ROOT / "scripts/generate_update_key.swift", key_file], capture_output=True, text=True).stdout.strip()
        if len(base64.b64decode(public, validate=True)) != 32: raise RuntimeError("Invalid public signing key")
        old_key = config.get("public_ed_key")
        if old_key and old_key != public: raise RuntimeError("Refusing unapproved signing key replacement")
        config["public_ed_key"] = public
        (ROOT / "packaging/update-config.json").write_text(json.dumps(config,indent=2)+"\n")
        info_path = app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info.setdefault("CFBundleVersion", info["CFBundleShortVersionString"])
        info.update({"SUFeedURL":config["feed_url"], "SUPublicEDKey":public,
                     "SUEnableAutomaticChecks":False, "SUAutomaticallyUpdate":False,
                     "SUAllowsAutomaticUpdates":False, "SUEnableSystemProfiling":False,
                     "SURequireSignedFeed":True, "SUVerifyUpdateBeforeExtraction":True,
                     "SUSignedFeedFailureExpirationInterval":0})
        info_path.write_bytes(plistlib.dumps(info))
        notices = app / "Contents/Resources/ThirdPartyLicenses"
        notices.mkdir(exist_ok=True)
        shutil.copy2(sparkle / "LICENSE", notices / "Sparkle.txt")
        if (ROOT / "LICENSE").is_file():
            shutil.copy2(ROOT / "LICENSE", app / "Contents/Resources/Project-LICENSE.txt")
        from importlib.metadata import distribution
        for name in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip", "PyInstaller"):
            package = distribution(name)
            for entry in package.files or ():
                if entry.name in {"LICENSE", "COPYING.txt"} and ".dist-info" in str(entry):
                    shutil.copy2(package.locate_file(entry), notices / (name + "-" + entry.name))
        run(["codesign", "--force", "--sign", "-", framework_dir / "libBackyardUpdater.dylib"])
        run(["codesign", "--force", "--sign", "-", app])
        run(["codesign", "--verify", "--deep", "--strict", app])
        version = info["CFBundleShortVersionString"]
        assets = out / "release-assets"; assets.mkdir(exist_ok=resume)
        archive = assets / f"BackyardVisitor-{version}-macOS-{platform.machine()}.zip"
        checkpoint("packaging", version=version)
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive])
        signing_tool = sparkle / "bin/sign_update"
        signature = run([signing_tool,"--ed-key-file",key_file,"-p",archive],capture_output=True,text=True).stdout.strip()
        run([signing_tool,"--ed-key-file",key_file,"--verify",archive,signature])
        rss=ET.Element("rss",{"version":"2.0"}); channel=ET.SubElement(rss,"channel")
        ET.SubElement(channel,"title").text="后院访客 · 软件更新"
        item=ET.SubElement(channel,"item")
        ET.SubElement(item,"title").text=f"后院访客 {version}"
        ET.SubElement(item,"pubDate").text=format_datetime(datetime.now(timezone.utc))
        ET.SubElement(item,f"{{{NS}}}version").text=info["CFBundleVersion"]
        ET.SubElement(item,f"{{{NS}}}shortVersionString").text=version
        ET.SubElement(item,f"{{{NS}}}minimumSystemVersion").text="12.0.0"
        if platform.machine()=="arm64": ET.SubElement(item,f"{{{NS}}}hardwareRequirements").text="arm64"
        ET.SubElement(item,"description").text="加入软件内检查更新、签名验证和更新前保存进度。日常运行仍可离线。"
        ET.SubElement(item,"enclosure",{"url":f"https://github.com/{config['repository']}/releases/download/v{version}/{archive.name}",f"{{{NS}}}edSignature":signature,"length":str(archive.stat().st_size),"type":"application/octet-stream"})
        feed=assets/"appcast.xml"
        ET.ElementTree(rss).write(feed,encoding="utf-8",xml_declaration=True)
        run([signing_tool,"--ed-key-file",key_file,feed])
        run([signing_tool,"--ed-key-file",key_file,"--verify",feed])
        (assets/(archive.name+".sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+"  "+archive.name+"\n")
        licence=next((p for p in (sparkle/'LICENSE',sparkle/'LICENSE.txt') if p.exists()),None)
        if licence: shutil.copy2(licence,assets/'Sparkle-LICENSE.txt')
        checkpoint("complete",version=version,app=str(app),archive=str(archive),signed_feed=str(feed),public_key=public,installed=False,published=False,apple_developer_id=False)
    except Exception as exc:
        checkpoint("failed",error=str(exc))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--sparkle",type=Path,required=True)
    p.add_argument("--resume-packaging", action="store_true", help="Resume packaging a previously built candidate without rebuilding source")
    a=p.parse_args()
    out=a.output.resolve()
    if not out.is_relative_to(ROOT / 'output'):
        p.error("Output must be inside this project's output/ directory")
    build(out,a.sparkle.resolve(),a.resume_packaging)

if __name__=="__main__": main()
