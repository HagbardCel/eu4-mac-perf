import Foundation
import ScreenCaptureKit
import AppKit

@main
struct WindowCapture {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 3, let pid = pid_t(args[1]) else {
            FileHandle.standardError.write("usage: eu4_window_sck <pid> <out.png>\n".data(using: .utf8)!)
            exit(2)
        }
        let output = args[2]
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
            let windows = content.windows.filter {
                $0.owningApplication?.processID == pid && $0.frame.width >= 100
                    && $0.frame.height >= 100
            }
            guard let window = windows.max(by: {
                $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height
            }) else {
                FileHandle.standardError.write("no window\n".data(using: .utf8)!)
                exit(4)
            }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = Int(window.frame.width * 2)
            config.height = Int(window.frame.height * 2)
            config.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter, configuration: config)
            let rep = NSBitmapImageRep(cgImage: image)
            guard let data = rep.representation(using: .png, properties: [:]) else {
                exit(6)
            }
            try data.write(to: URL(fileURLWithPath: output))
            print("{\"window_id\":\(window.windowID),\"width\":\(image.width),\"height\":\(image.height)}")
        } catch {
            FileHandle.standardError.write("error: \(error)\n".data(using: .utf8)!)
            exit(5)
        }
    }
}
