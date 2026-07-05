// Renders the app icon: rounded-square indigo→violet gradient with a white
// sparkle, following macOS icon-grid margins. Usage: swift render-icon.swift <out.png>
import AppKit

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon-1024.png"
let S: CGFloat = 1024

let img = NSImage(size: NSSize(width: S, height: S))
img.lockFocus()
guard let ctx = NSGraphicsContext.current?.cgContext else { fatalError("no ctx") }

// macOS icons float in a transparent canvas with ~10% margin
let margin = S * 0.10
let rect = CGRect(x: margin, y: margin, width: S - 2 * margin, height: S - 2 * margin)
let path = CGPath(roundedRect: rect, cornerWidth: rect.width * 0.225,
                  cornerHeight: rect.width * 0.225, transform: nil)
ctx.addPath(path)
ctx.clip()

let colors = [NSColor(calibratedRed: 0.388, green: 0.400, blue: 0.945, alpha: 1).cgColor,
              NSColor(calibratedRed: 0.545, green: 0.361, blue: 0.965, alpha: 1).cgColor]
let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                      colors: colors as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(grad,
                       start: CGPoint(x: rect.minX, y: rect.maxY),
                       end: CGPoint(x: rect.maxX, y: rect.minY), options: [])

// sparkle: 4-point star (concave diamond), centered
func sparkle(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat) -> CGPath {
    let p = CGMutablePath()
    let k = r * 0.28  // waist
    p.move(to: CGPoint(x: cx, y: cy + r))
    p.addQuadCurve(to: CGPoint(x: cx + r, y: cy), control: CGPoint(x: cx + k, y: cy + k))
    p.addQuadCurve(to: CGPoint(x: cx, y: cy - r), control: CGPoint(x: cx + k, y: cy - k))
    p.addQuadCurve(to: CGPoint(x: cx - r, y: cy), control: CGPoint(x: cx - k, y: cy - k))
    p.addQuadCurve(to: CGPoint(x: cx, y: cy + r), control: CGPoint(x: cx - k, y: cy + k))
    p.closeSubpath()
    return p
}
ctx.setFillColor(NSColor.white.cgColor)
ctx.addPath(sparkle(S * 0.47, S * 0.50, S * 0.23))
ctx.fillPath()
ctx.setFillColor(NSColor.white.withAlphaComponent(0.9).cgColor)
ctx.addPath(sparkle(S * 0.68, S * 0.68, S * 0.085))
ctx.fillPath()

img.unlockFocus()

guard let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else { fatalError("encode") }
try! png.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
