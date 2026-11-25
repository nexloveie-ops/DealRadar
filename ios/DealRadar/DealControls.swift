import SwiftUI

/// Tabs (All · Amazon · Networks ▾ · Shops ▾ · Mobile123), Filters, sort and categories — on Deals and My deals.
struct DealControls: View {
    @ObservedObject var store: DealsStore
    @State private var showFilters = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            tabs
            categories
        }
        .sheet(isPresented: $showFilters) { FiltersSheet(store: store).presentationDetents([.medium]) }
    }

    // All · Amazon · Networks ▾ · Shops ▾ · Mobile123, then Filters and sort.
    private var tabs: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Each tab is as wide as its name; the spare room is shared out between them.
            HStack(spacing: 0) {
                ForEach(DealsStore.Kind.allCases) { k in
                    if k != .all { Spacer(minLength: 2) }
                    if let all = k.menuTitle {
                        Menu {
                            Button { store.choose(k) } label: { Text("\(all) (\(store.shops(for: k).reduce(0) { $0 + $1.1 }))") }
                            Divider()
                            ForEach(store.shops(for: k), id: \.0) { s in
                                Button { store.choose(k, shop: s.0) } label: {
                                    if store.kind == k && store.shop == s.0 { Label("\(s.0) (\(s.1))", systemImage: "checkmark") }
                                    else { Text("\(s.0) (\(s.1))") }
                                }
                            }
                        } label: {
                            tabLabel(store.kind == k && store.shop != nil ? store.shop! : k.label, on: store.kind == k, menu: true)
                        }
                    } else {
                        Button { store.choose(k) } label: { tabLabel(k.label, on: store.kind == k, menu: false) }
                    }
                }
            }
            .padding(3)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.drLine))

            HStack {
                Button { showFilters = true } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "line.3.horizontal.decrease")
                        Text("Filters")
                        if store.activeFilters > 0 {
                            Text("\(store.activeFilters)").font(.caption.bold()).foregroundStyle(.white)
                                .frame(minWidth: 18, minHeight: 18).background(Color.drBlue, in: Capsule())
                        }
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Color.white, in: Capsule())
                    .overlay(Capsule().stroke(Color.drLine))
                }
                .foregroundStyle(Color.drInk)
                Spacer()
                Menu {
                    Picker("Sort by", selection: $store.sort) {
                        ForEach(DealsStore.Sort.allCases) { Text($0.label).tag($0) }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(store.sort.label)
                        Image(systemName: "chevron.down").font(.caption.weight(.semibold))
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Color.white, in: Capsule())
                    .overlay(Capsule().stroke(Color.drLine))
                }
                .foregroundStyle(Color.drInk)
            }
        }
    }

    private func tabLabel(_ text: String, on: Bool, menu: Bool) -> some View {
        HStack(spacing: 3) {
            Text(text).lineLimit(1).truncationMode(.tail).frame(maxWidth: 96).fixedSize(horizontal: text.count <= 10, vertical: false)
            if menu { Image(systemName: "chevron.down").font(.system(size: 8, weight: .bold)) }
        }
        .font(.system(size: 14, weight: .medium))
        .foregroundStyle(on ? .white : Color.drMuted)
        .padding(.vertical, 7)
        .padding(.horizontal, 9)
        .background(on ? Color.drBlue : .clear, in: RoundedRectangle(cornerRadius: 6))
    }

    private var categories: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(store.categories, id: \.0) { c in
                    let on = store.category == c.0
                    Button { store.category = c.0 } label: {
                        HStack(spacing: 4) {
                            Text(c.0)
                            Text("\(c.1)").foregroundStyle(on ? Color(hex: 0xC9CFDA) : Color.drStrike)
                        }
                        .font(.subheadline)
                        .foregroundStyle(on ? .white : Color.drInk)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(on ? Color.drInk : .white, in: Capsule())
                        .overlay(Capsule().stroke(on ? Color.drInk : Color.drLine))
                    }
                }
            }
        }
    }

}
