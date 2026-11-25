import SwiftUI
import SafariServices

/// One deal, laid out like a card on dealradar.ie: picture, shop, name, price tag, saving, where the price came from.
struct DealCard: View {
    let deal: Deal
    var showWhy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack {
                Color.white
                if let s = deal.imageUrl, let url = URL(string: s) {
                    AsyncImage(url: url) { phase in
                        if let img = phase.image { img.resizable().scaledToFit() }
                        else if phase.error != nil { placeholder }
                        else { ProgressView().tint(.drLine) }
                    }
                    .padding(8)
                } else {
                    placeholder
                }
            }
            .frame(height: 124)
            .clipped()

            Divider().overlay(Color.drShelf)

            VStack(alignment: .leading, spacing: 6) {
                Text(deal.shop)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(Color.drBlue)
                    .lineLimit(1)
                Text(deal.name)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.drInk)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, minHeight: 36, alignment: .topLeading)

                if let p = deal.priceMinor {
                    HStack(spacing: 6) {
                        PriceTag(minor: p, size: 21)
                        if let r = deal.referenceMinor, deal.discountPercent != nil {
                            Text(Format.eur(r)).font(.footnote).strikethrough().foregroundStyle(Color.drStrike).lineLimit(1)
                        }
                    }
                    if let s = deal.saving, let pct = deal.discountPercent {
                        Text("Save \(Format.eur(s)) (\(Int(pct.rounded()))% off)")
                            .font(.caption.weight(.bold)).foregroundStyle(Color.drInk)
                    }
                } else if let summary = deal.summaryEn {
                    Text(summary).font(.caption.weight(.bold))
                }

                if let cond = condition {
                    Text(cond).font(.caption2).foregroundStyle(Color.drInk)
                }
                if showWhy, let why = deal.why, !why.isEmpty {
                    WhyChips(reasons: why)
                }
                Text(source)
                    .font(.caption2)
                    .foregroundStyle(Color.drMuted)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 0)
                Divider().overlay(Color.drShelf)
                HStack {
                    Text("View at \(deal.shopShort)").font(.caption.weight(.bold)).foregroundStyle(Color.drBlue).lineLimit(1)
                    Spacer(minLength: 4)
                    if deal.affiliate == true { Text("Affiliate").font(.caption2).foregroundStyle(Color.drStrike) }
                }
            }
            .padding(10)
        }
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .contentShape(RoundedRectangle(cornerRadius: 8))
    }

    private var placeholder: some View {
        Text(deal.shop).font(.headline).foregroundStyle(Color.drLine)
    }

    private var condition: String? {
        switch deal.conditionCode {
        case "port_in":
            return "When you switch to \(deal.shop)" + (deal.minTopUpMinor.map { ", with a \(Format.eur($0)) top-up" } ?? "")
        case "clubcard":
            return "With Tesco Clubcard"
        default:
            return nil
        }
    }

    private var source: String {
        let checked = Format.when(deal.checkedAt)
        switch deal.priceSource {
        case "third_party":
            return "Price reported by \(deal.sourceSite ?? "a deals site"), \(checked). Check Amazon for today's price."
        case "own_store":
            return "Mobile123 price, checked \(checked)"
        case "operator":
            if let e = Format.date(deal.endsOn) { return "Offer ends \(Format.when(ISO8601DateFormatter().string(from: e)))" }
            return "Price checked \(checked)"
        default:
            if deal.referenceKind == "price_drop", let seen = deal.referenceSeenOn {
                return "Price drop: we saw \(Format.eur(deal.referenceMinor)) here on \(Format.when(seen)). Checked \(checked)"
            }
            return "Price checked \(checked)"
        }
    }
}

struct WhyChips: View {
    let reasons: [Reason]
    var body: some View {
        FlowRow(spacing: 4) {
            ForEach(Array(reasons.enumerated()), id: \.offset) { _, r in
                switch r.type {
                case "watch": Pill(text: r.label ?? "Target reached", fg: .drInk, bg: .drTag)
                case "keyword": Pill(text: "“\(r.label ?? "")”" + (r.isPrivate == true ? " · only you" : ""))
                default: Pill(text: r.label ?? "")
                }
            }
        }
    }
}

/// Lays children out in rows, wrapping to the next row when one is full.
struct FlowRow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0, x + s.width > width { y += row + spacing; x = 0; row = 0 }
            x += s.width + spacing
            row = max(row, s.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > bounds.minX, x + s.width > bounds.maxX { y += row + spacing; x = bounds.minX; row = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            row = max(row, s.height)
        }
    }
}

/// Opens a shop page inside the app; affiliate links keep working.
struct SafariView: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController {
        let c = SFSafariViewController(url: url)
        c.preferredControlTintColor = UIColor(Color.drBlue)
        return c
    }
    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}

extension URL: @retroactive Identifiable {
    public var id: String { absoluteString }
}

/// Grid of deal cards that opens the shop when one is tapped.
struct DealGrid: View {
    let deals: [Deal]
    var showWhy = false
    @State private var open: URL?

    private let columns = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            ForEach(deals) { d in
                Button {
                    if let s = d.buyUrl, let u = URL(string: s) {
                        API.recordClick(d.id)
                        open = u
                    }
                } label: {
                    DealCard(deal: d, showWhy: showWhy).frame(maxHeight: .infinity, alignment: .top)
                }
                .buttonStyle(.plain)
            }
        }
        .sheet(item: $open) { url in SafariView(url: url).ignoresSafeArea() }
    }
}
