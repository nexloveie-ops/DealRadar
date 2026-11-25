import SwiftUI

struct MyDealsView: View {
    @EnvironmentObject var auth: AuthManager
    @EnvironmentObject var member: MemberStore
    /// The same tabs, filters, sort and categories as the Deals tab, over the member's own deals.
    @StateObject private var store = DealsStore(mine: true)

    var body: some View {
        NavigationStack {
            Group {
                if !auth.signedIn {
                    SignInView()
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("Deals from the shops and networks you follow, your own search words and the products you watch.")
                                .font(.subheadline).foregroundStyle(Color.drMuted)
                            if !member.myDealsLoaded {
                                ProgressView("Loading your deals…").frame(maxWidth: .infinity).padding(.top, 50)
                            } else if member.myDeals.isEmpty {
                                VStack(spacing: 6) {
                                    Text("Nothing you follow is on offer right now").font(.headline)
                                    Text("Prices are checked twice a day. Add words or products in Settings.")
                                        .font(.footnote).foregroundStyle(Color.drMuted).multilineTextAlignment(.center)
                                }
                                .frame(maxWidth: .infinity).padding(.top, 50)
                            } else {
                                DealControls(store: store)
                                let list = store.visible
                                let hits = list.filter(\.targetReached).count
                                Text("\(list.count) deal\(list.count == 1 ? "" : "s")" + (hits > 0 ? " · \(hits) watched product\(hits == 1 ? "" : "s") at your target" : ""))
                                    .font(.footnote).foregroundStyle(Color.drMuted)
                                if list.isEmpty {
                                    VStack(spacing: 6) {
                                        Text("No deals match these filters").font(.headline)
                                        Text("Try another tab, a smaller discount or another category.")
                                            .font(.footnote).foregroundStyle(Color.drMuted).multilineTextAlignment(.center)
                                    }
                                    .frame(maxWidth: .infinity).padding(.top, 30)
                                } else {
                                    DealGrid(deals: list, showWhy: true)
                                }
                            }
                            if let e = member.error { Text(e).font(.footnote).foregroundStyle(Color(hex: 0xA32D2D)) }
                        }
                        .padding(16)
                    }
                    .background(Color.drShelf)
                    .refreshable { await member.loadMyDeals() }
                    .task { if !member.myDealsLoaded { await member.loadMyDeals() } }
                }
            }
            .navigationTitle("My deals")
            .toolbarBackground(.white, for: .navigationBar)
            .onAppear {
                store.deals = member.myDeals
                if member.myDealsStale { Task { await member.loadMyDeals() } }
            }
            .onChange(of: member.myDeals) { _, new in store.deals = new }
        }
    }
}
