import SwiftUI

struct MyDealsView: View {
    @EnvironmentObject var auth: AuthManager
    @EnvironmentObject var member: MemberStore

    var body: some View {
        NavigationStack {
            Group {
                if !auth.signedIn {
                    SignInView()
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
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
                                Text("\(member.myDeals.count) deals").font(.footnote).foregroundStyle(Color.drMuted)
                                DealGrid(deals: member.myDeals, showWhy: true)
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
        }
    }
}
