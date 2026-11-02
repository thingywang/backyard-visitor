// Create an app-specific Ed25519 seed without putting private material in logs or Keychain.
import Foundation
import CryptoKit
let path = URL(fileURLWithPath: CommandLine.arguments[1])
let manager = FileManager.default
try manager.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
let key: Curve25519.Signing.PrivateKey
if manager.fileExists(atPath: path.path) {
    let encoded = try String(contentsOf: path, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    guard let raw = Data(base64Encoded: encoded) else { fatalError("Invalid local update signing key") }
    key = try Curve25519.Signing.PrivateKey(rawRepresentation: raw)
} else {
    key = Curve25519.Signing.PrivateKey()
    try (key.rawRepresentation.base64EncodedString() + "\n").write(to: path, atomically: true, encoding: .utf8)
}
try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
print(key.publicKey.rawRepresentation.base64EncodedString())
