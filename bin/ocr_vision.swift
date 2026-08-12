import Foundation
import Vision
import ImageIO

guard CommandLine.arguments.count >= 2 else {
    fputs("usage: ocr_vision IMAGE\n", stderr)
    exit(64)
}

let url = URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL
guard let source = CGImageSourceCreateWithURL(url, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fputs("failed to load image\n", stderr)
    exit(65)
}

func makeRequest(languages: [String], correction: Bool) -> VNRecognizeTextRequest {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.revision = VNRecognizeTextRequestRevision3
    request.usesLanguageCorrection = correction
    request.minimumTextHeight = 0.004
    request.recognitionLanguages = languages
    return request
}

// Vision expects regional language identifiers. Passing the generic "ar"
// raises an opaque nilError on some macOS releases.
var request: VNRecognizeTextRequest? = nil
let attempts: [([String], Bool)] = [
    (["ar-SA", "en-US"], true),
    (["ar-SA"], false),
    (["ars-SA"], false),
    (["en-US"], false),
]
var errors: [String] = []
for (languages, correction) in attempts {
    let candidate = makeRequest(languages: languages, correction: correction)
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
    do {
        try handler.perform([candidate])
        request = candidate
        break
    } catch {
        errors.append("\(languages.joined(separator: "+")): \(error)")
    }
}
guard let completed = request else {
    fputs("vision OCR failed: \(errors.joined(separator: " | "))\n", stderr)
    exit(70)
}

guard let observations = completed.results else { exit(0) }
let sorted = observations.sorted { a, b in
    let aY = a.boundingBox.origin.y
    let bY = b.boundingBox.origin.y
    if abs(aY - bY) > 0.008 { return aY > bY }
    return a.boundingBox.origin.x > b.boundingBox.origin.x
}

for observation in sorted {
    let candidates = observation.topCandidates(3)
    guard let top = candidates.first else { continue }
    let box = observation.boundingBox
    let alternatives = candidates.dropFirst().map {
        ["text": $0.string, "confidence": Double($0.confidence)] as [String: Any]
    }
    let payload: [String: Any] = [
        "x": Double(box.origin.x),
        "y": Double(box.origin.y),
        "w": Double(box.size.width),
        "h": Double(box.size.height),
        "text": top.string,
        "confidence": Double(top.confidence),
        "alternatives": alternatives,
    ]
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}
