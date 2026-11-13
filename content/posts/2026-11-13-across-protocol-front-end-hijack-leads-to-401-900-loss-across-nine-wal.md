---
{
  "title": "Across Protocol Front-End Hijack Leads to $401,900 Loss Across Nine Wallets",
  "description": "An updated on-chain trace by Bitquery reveals that an Across Protocol website hijack resulted in $401,900 stolen across nine wallets, with $305,000 taken from a",
  "tags": [
    "Across Protocol",
    "Aave",
    "DeFi",
    "Security",
    "Hacks",
    "Bitquery"
  ],
  "date": "2026-11-13T07:32:19Z",
  "sources": [
    "https://cryptoslate.com/across-protocol-website-attack-reached-an-aave-deposit-new-402000-loss-trace-shows/"
  ],
  "source_names": [
    "CryptoSlate"
  ],
  "status": "published",
  "used_item_ids": [
    "0c62c95ecffe1e6a"
  ],
  "draft_of_batch": "20261113-072739",
  "image": "img/2026-11-13-across-protocol-front-end-hijack-leads-to-401-900-loss-across-nine-wal.jpg",
  "image_ai": true
}
---

# Across Protocol Front-End Hijack Leads to $401,900 Loss Across Nine Wallets

A front-end website security breach involving the cross-chain bridge Across Protocol resulted in the theft of an estimated $401,900 across nine distinct cryptocurrency wallets. According to an analytical update published by blockchain data platform Bitquery on November 12, 2026, the bulk of the compromised funds stems from a single high-value exploit targeting an Aave deposit token. The underlying details of the trace were initially reported by [CryptoSlate](https://cryptoslate.com/across-protocol-website-attack-reached-an-aave-deposit-new-402000-loss-trace-shows/).

The incident highlights persistent vulnerabilities in decentralized finance (DeFi) front-end infrastructure. Rather than an exploit of underlying smart contracts, website hijacks typically involve unauthorized access to a protocol's domain name system (DNS) or web hosting environment. This allows attackers to redirect users to malicious interfaces designed to drain wallet balances or trick users into signing unauthorized transactions.

## Details of the Incident and Stolen Funds

On November 12, 2026, Bitquery provided an updated on-chain analysis tracing the flow of stolen funds associated with the Across Protocol website attack. The investigation revealed that total losses reached approximately $401,900 spread across nine victim wallets.

The vast majority of the stolen assets came from a single address. A single Aave user lost roughly $305,000 in an Aave deposit token during the front-end attack. This single address accounts for over 75% of the total funds stolen in the incident. The remaining losses, totaling under $100,000, were distributed across eight other affected wallets that interacted with the hijacked web interface.

Website hijacks operate by altering the user interface presented to visitors. When users attempt to perform routine protocol actions—such as bridging assets, depositing funds, or providing liquidity—the malicious site presents fraudulent contract approvals. Once signed by the user, these approvals grant the attacker permission to transfer tokens directly out of the victim's wallet.

## Implications for DeFi Front-End Security

The exploit involving Across Protocol and Aave deposit tokens underscores a ongoing challenge within the decentralized finance ecosystem: the reliance on centralized web hosting and domain infrastructure. While smart contracts deployed on immutable blockchains are secured by cryptographic consensus, the web domains used to interact with these contracts often rely on traditional Web2 infrastructure.

Attackers frequently target domain registrars, content delivery networks (CDNs), or developer credentials to compromise the front end of a project without modifying its smart contract code. Because users rely heavily on web interfaces to construct transactions, a compromised front end can deceive market participants into approving unauthorized token transfers.

The involvement of an Aave deposit token in this specific breach demonstrates how cross-protocol interactions can compound user risk. Deposit tokens represent staked or deposited assets earning yield within lending protocols like Aave. When users interact with external bridges like Across Protocol while holding derivative or deposit tokens, a malicious front end can request approvals for these complex asset types alongside standard base tokens.

## Ecosystem Context and Security Protocols

Security incidents involving website hijacks have led security researchers and protocol developers to advocate for stronger domain protection protocols. Measures such as DNSSEC deployment, multi-factor authentication for domain registry changes, and decentralized front-end alternatives—like hosting interfaces on the InterPlanetary File System (IPFS)—are frequently recommended to mitigate front-end attacks.

Additionally, Web3 wallet providers have begun integrating transaction simulation and approval warnings to alert users when a web interface requests access to high-value assets, such as yield-bearing deposit tokens or liquidity provider positions. Despite these efforts, user interaction with compromised web interfaces remains a primary vector for capital losses across the cryptocurrency industry.

This article is for informational purposes only and does not constitute financial advice.

## Sources

* [CryptoSlate: Across Protocol website attack reached an Aave deposit; new $402,000 loss trace shows](https://cryptoslate.com/across-protocol-website-attack-reached-an-aave-deposit-new-402000-loss-trace-shows/)
