---
{
  "title": "Solana Quantum-Resistant Testnet Suffers 90% Throughput Drop, IOSCO Data Shows",
  "description": "A testnet implementation designed to make Solana quantum-resistant resulted in a roughly 90% decline in throughput, according to findings cited by IOSCO.",
  "tags": [
    "Solana",
    "Post-Quantum Cryptography",
    "IOSCO",
    "Blockchain Performance",
    "Crypto Infrastructure"
  ],
  "date": "2026-11-22T07:39:16Z",
  "sources": [
    "https://www.investing.com/news/cryptocurrency-news/bitcoin-struggles-below-60000-as-mining-profits-plunge-security-risks-grow-5043790"
  ],
  "source_names": [
    "Investing.com"
  ],
  "status": "published",
  "used_item_ids": [
    "91e845d8589554d0"
  ],
  "draft_of_batch": "20261122-073712",
  "image": "img/2026-11-22-solana-quantum-resistant-testnet-suffers-90-throughput-drop-iosco-data.jpg"
}
---

# Solana Quantum-Resistant Testnet Suffers 90% Throughput Drop, IOSCO Data Shows

Efforts to prepare blockchain networks for future quantum computing threats face major performance trade-offs. Findings highlighted by the International Organization of Securities Commissions (IOSCO) show that a testnet implementation designed to make the Solana blockchain quantum-resistant experienced a throughput drop of roughly 90%.

The significant drop in processing capacity highlights the friction between high-speed distributed ledgers and post-quantum security measures. While post-quantum cryptography (PQC) aims to shield decentralized networks from being compromised by quantum algorithms, integrating these mathematical safeguards can severely degrade network performance, as reported by [CryptoPotato](https://cryptopotato.com/quantum-proofing-crypto-could-come-at-a-staggering-cost/).

This outcome arrives as developers across the digital asset space attempt to balance scalability with long-term cryptographic security. This article is provided for educational and news reporting purposes only; this is not financial advice.

## The Threat of Quantum Computing to Public Key Encryption

Public blockchain networks currently rely on classical asymmetric cryptography—specifically public-key standards like the Elliptic Curve Digital Signature Algorithm (ECDSA)—to secure funds, authenticate network state changes, and validate user transactions across nodes.

While traditional computing hardware cannot break these cryptographic algorithms within a practical timeframe, advanced quantum computers running specialized routines, such as Shor's algorithm, could theoretically solve the discrete logarithm problems that protect elliptic curve cryptography. This capability would allow an attacker to derive private keys directly from public keys exposed on the ledger.

To address this potential risk, cryptographers have developed post-quantum cryptographic schemes. These frameworks rely on alternative mathematical problems—such as lattice-based cryptography—that are structured to resist attacks from both classical supercomputers and future quantum machines.

However, post-quantum algorithms introduce structural overhead. They generally require larger public key sizes, larger cryptographic signatures, and increased computation time for generating and verifying signatures compared to traditional standards like ECDSA.

## Impact on Solana Testnet Performance

Solana is architected as a high-throughput layer-1 network designed to process high transaction volumes with low latency. Its base operational model relies on light cryptographic signatures and rapid state validation to maximize network capacity.

When post-quantum encryption protocols were implemented on a Solana testnet, the additional computational and bandwidth requirements of PQC signatures led directly to the 90% reduction in network throughput cited by IOSCO.

In blockchain architectures, transaction speed depends on node processing capacity and block capacity limits. Because post-quantum digital signatures require significantly more data per transaction, fewer transactions fit within a single block. At the same time, network validators face higher CPU overhead to process and verify each individual signature, creating processing bottlenecks for networks engineered for high transaction speeds.

## Broad Operational and Infrastructure Costs

The testnet data cited in the IOSCO context demonstrates that transitioning decentralized infrastructure to post-quantum standards will not be a simple software update. Instead, adopting quantum resistance may impose substantial costs on network speed, validator hardware requirements, and user experience.

If layer-1 networks must accept lower transaction throughput to implement quantum-proof signatures, high-frequency decentralized applications, automated market makers, and real-time payment protocols built on low latency could face execution constraints.

Proposed technical solutions to bridge this performance gap include hardware acceleration for post-quantum signature verification, optimizing zero-knowledge proof systems for PQC environments, and offloading signature aggregation to layer-2 or off-chain systems. However, balancing post-quantum security guarantees with high operational capacity remains an active engineering challenge for the industry.

## Regulatory Scrutiny and Future Implementation

International standard-setting entities such as IOSCO are increasingly evaluating the operational resilience of public blockchain networks. Regulators examining infrastructure risks are assessing both near-term network stability and long-term technical adaptability against emerging computing capabilities.

The performance data from Solana's post-quantum testnet highlights that widespread adoption of post-quantum standards will require continued research and testing. Blockchain developers and institutional participants will need to identify technical architectures that maintain network functionality without leaving distributed ledgers exposed to future quantum developments.

## Sources

* [CryptoPotato - Quantum-Proofing Crypto Could Come at a Staggering Cost](https://cryptopotato.com/quantum-proofing-crypto-could-come-at-a-staggering-cost/)
