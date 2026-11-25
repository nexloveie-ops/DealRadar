import SwiftUI

// Colours from dealradar.ie, so the app and the website look like one product.
extension Color {
    init(hex: UInt32) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
    static let drBlue = Color(hex: 0x3565E0)
    static let drInk = Color(hex: 0x16213A)
    static let drMuted = Color(hex: 0x5B6578)
    static let drStrike = Color(hex: 0x8A93A3)
    static let drShelf = Color(hex: 0xEEF1F5)
    static let drLine = Color(hex: 0xDCE1EA)
    static let drTag = Color(hex: 0xFFD43B)
    static let drGreen = Color(hex: 0x16794A)
    static let drGreenBg = Color(hex: 0xE5F3EB)
    static let drOrange = Color(hex: 0xB25A07)
    static let drOrangeBg = Color(hex: 0xFDF0E2)
}

enum Format {
    private static let money: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_IE")
        f.numberStyle = .decimal
        f.minimumFractionDigits = 0
        f.maximumFractionDigits = 2
        return f
    }()

    /// 4999 -> "€49.99", 5900 -> "€59"
    static func eur(_ minor: Int?) -> String {
        guard let minor else { return "" }
        let f = money
        f.minimumFractionDigits = minor % 100 == 0 ? 0 : 2
        return "€" + (f.string(from: NSNumber(value: Double(minor) / 100)) ?? "")
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain = ISO8601DateFormatter()

    static func date(_ s: String?) -> Date? {
        guard let s else { return nil }
        if let d = iso.date(from: s) ?? isoPlain.date(from: s) { return d }
        // Dates without a time, e.g. "2027-01-05"
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_IE")
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Europe/Dublin")
        return f.date(from: String(s.prefix(10)))
    }

    private static let dublin = TimeZone(identifier: "Europe/Dublin")!

    /// "15:40 today", "tomorrow at 07:00" or "26 Nov", Irish time.
    static func when(_ s: String?, withTime: Bool = false) -> String {
        guard let d = date(s) else { return "" }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = dublin
        let time = DateFormatter()
        time.timeZone = dublin
        time.dateFormat = "HH:mm"
        let dayF = DateFormatter()
        dayF.timeZone = dublin
        dayF.locale = Locale(identifier: "en_IE")
        dayF.dateFormat = "d MMM"
        if cal.isDateInToday(d) { return withTime ? "today at \(time.string(from: d))" : "\(time.string(from: d)) today" }
        if cal.isDateInTomorrow(d) { return "tomorrow at \(time.string(from: d))" }
        return withTime ? "\(dayF.string(from: d)) at \(time.string(from: d))" : dayF.string(from: d)
    }

    /// Time left until a date: "2 days 4 h", "5 h 12 min", "8 min".
    static func span(until s: String?) -> String? {
        guard let d = date(s) else { return nil }
        let m = Int(d.timeIntervalSinceNow / 60)
        if m <= 0 { return nil }
        let days = m / 1440, h = (m % 1440) / 60, mm = m % 60
        if days > 0 { return "\(days) day\(days == 1 ? "" : "s")" + (h > 0 ? " \(h) h" : "") }
        if h > 0 { return "\(h) h \(mm) min" }
        return "\(mm) min"
    }
}

/// The yellow price tag from the website: a label with a notched left edge and a punched hole.
struct TagShape: Shape {
    func path(in r: CGRect) -> Path {
        let notch = r.height * 0.42
        var p = Path()
        p.move(to: CGPoint(x: notch, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.maxY))
        p.addLine(to: CGPoint(x: notch, y: r.maxY))
        p.addLine(to: CGPoint(x: r.minX, y: r.midY))
        p.closeSubpath()
        return p
    }
}

struct PriceTag: View {
    let minor: Int
    var size: CGFloat = 22

    var body: some View {
        let text = Format.eur(minor).dropFirst()
        HStack(alignment: .firstTextBaseline, spacing: 1) {
            Text("€").font(.system(size: size * 0.6, weight: .bold).width(.condensed))
            Text(text).font(.system(size: size, weight: .bold).width(.condensed)).monospacedDigit()
        }
        .foregroundStyle(Color.drInk)
        .padding(.leading, size * 0.75)
        .padding(.trailing, size * 0.4)
        .padding(.vertical, size * 0.18)
        .background(
            TagShape().fill(Color.drTag)
                .overlay(alignment: .leading) {
                    Circle().fill(.white).frame(width: size * 0.22, height: size * 0.22).padding(.leading, size * 0.3)
                }
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Format.eur(minor))
    }
}

struct Pill: View {
    let text: String
    var fg: Color = .drMuted
    var bg: Color = .drShelf

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .foregroundStyle(fg)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(bg, in: Capsule())
    }
}
