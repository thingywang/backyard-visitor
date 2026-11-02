"""Offline, user-authorized color-only candidate; never edits production assets.

Match warm-fur chroma to standing.png in comparable lightness bins. One bounded
Lab offset is shared by every run pose. Luminance, geometry and alpha stay fixed
before RGB quantization. No spatial filter, resampling, sharpening or new pixels.
Requires Pillow/numpy only in the offline workspace runtime, not in the app.
"""
from pathlib import Path
import hashlib
import json
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/experiments/color'
M = np.array([[.4124564,.3575761,.1804375], [.2126729,.7151522,.0721750], [.0193339,.1191920,.9503041]])
WHITE = np.array([.95047,1.,1.08883])
EPS = 216/24389
KAPPA = 24389/27


def lab(rgb):
    x = rgb.astype(np.float64)/255
    linear = np.where(x <= .04045, x/12.92, ((x+.055)/1.055)**2.4)
    xyz = linear @ M.T / WHITE
    f = np.where(xyz > EPS, np.cbrt(xyz), (KAPPA*xyz+16)/116)
    return np.stack((116*f[...,1]-16, 500*(f[...,0]-f[...,1]), 200*(f[...,1]-f[...,2])), axis=-1)


def rgb(lab_value):
    fy = (lab_value[...,0]+16)/116
    f = np.stack((fy+lab_value[...,1]/500,fy,fy-lab_value[...,2]/200),axis=-1)
    xyz = np.where(f**3 > EPS, f**3, (116*f-16)/KAPPA)*WHITE
    linear = xyz @ np.linalg.inv(M).T
    srgb = np.where(linear <= .0031308, 12.92*linear, 1.055*np.maximum(linear,0)**(1/2.4)-.055)
    return np.clip(np.rint(srgb*255),0,255).astype(np.uint8)


def ramp(x,lo,hi):
    t=np.clip((x-lo)/(hi-lo),0,1)
    return t*t*(3-2*t)


def selection(a, values):
    L,aa,bb = np.moveaxis(values,-1,0)
    # Exclude transparency, neutral whiskers/black stripes, pink ears and green eyes.
    return (a[...,3]>200)&(L>25)&(L<75)&(aa>3)&(bb>8)&(bb>aa*1.05)


def main():
    source_path=ROOT/'backyard_visitor/assets/run.png'
    reference_path=ROOT/'backyard_visitor/assets/standing.png'
    source=np.array(Image.open(source_path).convert('RGBA'))
    reference=np.array(Image.open(reference_path).convert('RGBA'))
    src_lab,ref_lab=lab(source[...,:3]),lab(reference[...,:3])
    sm,rm=selection(source,src_lab),selection(reference,ref_lab)
    offsets=[];bins=[]
    for lo in range(25,75,10):
        ss=sm&(src_lab[...,0]>=lo)&(src_lab[...,0]<lo+10)
        rr=rm&(ref_lab[...,0]>=lo)&(ref_lab[...,0]<lo+10)
        if min(ss.sum(),rr.sum())<100:continue
        a=np.median(src_lab[ss,1:],axis=0);b=np.median(ref_lab[rr,1:],axis=0)
        offsets.append(b-a)
        bins.append({'L_range':[lo,lo+10],'source_pixels':int(ss.sum()),'reference_pixels':int(rr.sum()),'source_median_ab':a.tolist(),'reference_median_ab':b.tolist()})
    if len(offsets)<3:raise RuntimeError('Not enough comparable fur samples')
    # Conservative correction, not a full distribution/brightness transplant.
    delta=np.clip(np.median(offsets,axis=0)*.8,[-4,-5],[4,5])
    L,a,b=np.moveaxis(src_lab,-1,0)
    weight=(ramp(a,1,5)*ramp(b,4,12)*ramp(b-a,.5,6)*ramp(L,8,25)*(1-ramp(L,82,96)))
    weight=np.where(source[...,3]>0,weight,0)
    corrected=src_lab.copy();corrected[...,1:]+=weight[...,None]*delta
    candidate=source.copy();converted=rgb(corrected)
    changed_region=weight>0
    candidate[changed_region,:3]=converted[changed_region]
    OUT.mkdir(parents=True,exist_ok=True)
    output=OUT/'run-calibrated-candidate-3.png'
    Image.fromarray(candidate).save(output)
    saved=np.array(Image.open(output).convert('RGBA'))
    assert saved.shape==source.shape
    assert np.array_equal(saved[...,3],source[...,3]), 'Alpha changed'
    assert np.array_equal(saved[~changed_region],source[~changed_region]), 'Protected pixels changed'
    assert np.array_equal(saved[source[...,3]==0],source[source[...,3]==0]), 'Empty pixels changed'
    actual_lab=lab(saved[...,:3]);le=np.abs(actual_lab[...,0]-src_lab[...,0])[changed_region]
    report={'method':'One bounded Lab chroma offset with smooth warm-fur selection; no spatial operations','authorized_by_user':True,'production_replaced':False,'source':str(source_path),'reference':str(reference_path),'candidate':str(output),'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),'reference_sha256':hashlib.sha256(reference_path.read_bytes()).hexdigest(),'candidate_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'size':[saved.shape[1],saved.shape[0]],'matched_lightness_bins':bins,'applied_ab_offset':delta.tolist(),'alpha_identical':True,'all_unselected_pixels_identical':True,'fully_transparent_rgba_identical':True,'pixel_resampling':False,'sharpening':False,'lightness_error_from_rgb_quantization':{'max':float(le.max()),'mean':float(le.mean())},'note':'Pixel position and alpha are exact. Chroma changes can alter perceived fur contrast; visual review is still required. This calibrates running only, not all pose atlases.'}
    (OUT/'run-color-programmatic-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('candidate','applied_ab_offset','alpha_identical','lightness_error_from_rgb_quantization')},ensure_ascii=False))


if __name__=='__main__':main()
