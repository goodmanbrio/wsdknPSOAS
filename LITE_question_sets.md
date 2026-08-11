Created: 2026-08-10
Updated: 2026-08-10

# LITE question sets — chained retrieval and follow-up evaluation

These sets are designed for the PSOAS session runner. They use the ingested
LITE documents plus the optical-market documents under `data/files_ingested/`.
Each set is a separate mock analyst session. Questions within a set should be
asked in order so the follow-ups test session memory, numerical carry-forward,
source attribution, and the ability to connect evidence across documents.

The source folder contains both company materials and sell-side research from
different dates. A correct answer should label the source and date when figures
or estimates conflict. It should not silently combine Mizuho's November 2025
F27E framework with Morgan Stanley's May 2026 CY28 framework.

---

## Set 1 — Earnings inflection, margin expansion, and the FY26 bridge

Primary sources: Lumentum Q3 FY26 earnings release and presentation; LITE
earnings notes; Rosenblatt and Morgan Stanley research reports.

| # | Question | Expected answer points | Expected answer chunks |
|---|---|---|---|
| 1 | Walk me through LITE's earnings inflection from FY25 Q4 through FY26 Q3, then show the Q4 FY26 guide. Include revenue, non-GAAP gross margin, non-GAAP operating margin, and non-GAAP EPS where available. | FY25 Q4 revenue $481M, GM 37.8%, OM 15.0%, EPS $0.88. FY26 Q1 revenue $533.8M, GM 39.4%, OM 18.7%, EPS $1.10. FY26 Q2 revenue $665.5M, GM 42.5%, OM 25.2%, EPS $1.67. FY26 Q3 revenue $808.4M, GM 47.9%, OM 32.2%, EPS $2.37. Q4 guide is revenue $960M–$1.01B, OM 35.0%–36.0%, EPS $2.85–$3.05; the company does not provide a Q4 non-GAAP GM guide in this material. | `LITE/Notes/Lumentum (LITE US) 4Q beat...md`, lines 24–28; `LITE/Notes/Lumentum (LITE US) beat on 1Q...md`, lines 24–28; `LITE/Company/Lumentum-Announces...2026.md`, lines 47–55 and 69–75. |
| 2 | What explains the margin expansion even as CloudLight and transceiver revenue accelerated? | The sources point to pricing discipline/power in constrained EML, pump-laser, and DCI products; favorable mix toward 200G EMLs, laser chips, and scale-across components; operating leverage; and later internal CW-laser integration. Cloud transceivers historically carried lower margins, so the answer should explain that higher-margin EML and component mix offset dilution rather than claim every growth product had high margins. | Q3 release, lines 27–29; Morgan Stanley pricing report, lines 79–88; Rosenblatt margins report, lines 20–26 and 30–38; 4Q note, lines 31 and 63–64. |
| 3 | Separate what is already demonstrated from what is still an estimate in the FY26-to-FY27 setup. | Q1–Q3 FY26 are reported actuals; Q4 FY26 is guidance. Demonstrated drivers include record EML shipments, strong Cloud transceiver shipments, DCI/scale-across growth, and initial OCS ramp. Still forward-looking are the full 1.6T layering effect, meaningful CW integration, OCS scaling toward roughly $100M per quarter, and CPO becoming material. | Q3 presentation, lines 32–52 and 87–96; Q3 release, lines 67–75; Q3 note, lines 35–59. |
| 4 | How did the business mix change in Q3 FY26, and why does that matter for the margin outlook? | Components were $533.3M, or 66% of revenue, up 20.2% Q/Q and 77.3% Y/Y. Systems were $275.1M, or 34%, up 24.0% Q/Q and 121.1% Y/Y. Systems growth was led by record Cloud transceiver shipments, while Components benefited from EML and DCI components. The answer should connect mix to both operating leverage and the risk that lower-margin transceivers can slow the margin step-up. | Q3 release, lines 47–63; Q3 presentation, lines 40–52 and 60–66; Morgan Stanley pricing report, lines 86–90. |
| 5 | If Q4 FY26 revenue reaches the midpoint of guidance, calculate the sequential revenue growth and the midpoint operating-margin expansion versus Q3. | Midpoint revenue is $985M. Sequential growth is approximately 21.8% versus $808.4M. Midpoint operating margin is 35.5%, or 3.3 percentage points above Q3's 32.2%. Do not calculate a Q4 gross margin from the materials because only an implied margin appears in an analyst note, not company guidance. | Q3 release, lines 53–55 and 69–75; Q3 presentation, lines 118–129; Q3 earnings note, lines 41–51. |
| 6 | What could cause revenue to keep growing while margins disappoint? Give the mechanism, not just a list of risks. | Cloud transceiver mix can dilute margins because transceiver GM was described as roughly 30%/mid-20s; EML/CW competition can reduce pricing; the EML shortage can normalize earlier; InP capacity may be diverted between EML and CW; OCS supply-chain constraints can delay a high-margin ramp; and tariffs, costs, or product-mix changes can offset operating leverage. | Morgan Stanley pricing report, lines 81–88 and 96; Q3 release, lines 85–87; Morgan Stanley risk-reward, lines 456–460 and 501–514. |

**Set 1 follow-up behavior:** Turn 5 tests arithmetic using the prior answer. Turn 6 should explicitly refer back to the mix discussion in turns 2 and 4.

---

## Set 2 — From 800G to 1.6T: EML, CW, transceivers, OCS, and CPO

Primary sources: Mizuho LITE initiation; Jefferies COHR/LITE optical report;
LITE conference notes; optical-transceiver and datacenter-connectivity files.

| # | Question | Expected answer points | Expected answer chunks |
|---|---|---|---|
| 1 | Explain the role of EML lasers, CW lasers, optical transceivers, OCS, and CPO in LITE's AI-networking thesis. | EMLs are high-speed InP laser components used in 800G/1.6T transceiver architectures and are LITE's established strength. CW lasers are an alternative/companion laser approach, with LITE working toward internal integration in its own transceivers. Transceivers connect electrical and optical domains. OCS provides optical switching between racks for scale-out/scale-up use cases. CPO places optics closer to the switch and relies on high-power laser content, with LITE positioned as a laser supplier, especially for NVDA-related systems. | Mizuho initiation, lines 22–30 and 201–205; Jefferies report, lines 56–57 and 98–104; LITE MS fireside note, lines 24–41. |
| 2 | Connect the 400G/800G to 1.6T/3.2T roadmap to LITE's revenue and ASP opportunity. | Higher bandwidth requires more optical content and higher-power lasers. The transition is described as 800G at 100G per lane today and 1.6T at 200G per lane next. Mizuho expects 50%–100% ASP uplifts per laser generation and laser revenue rising from roughly $1B in FY25 to $2.2B in FY28E, or roughly 54% of revenue. The optical-transceiver market file shows 1.6T TAM rising rapidly while 800G remains large. | Mizuho initiation, lines 201–205; optical-transceivers file, lines 17–84; Jefferies report, lines 20 and 76. |
| 3 | Distinguish OCS from CPO by use case, customer, timing, revenue opportunity, and margin. | OCS is associated with rack-to-rack optical switching, Google scale-out/scale-up, and a broader hyperscaler pipeline; sources cite a ramp toward $100M per quarter and a roughly $400M+ backlog, with more than 50% GM in Mizuho's framework. CPO is associated with switch-level optical packaging/high-power lasers, particularly NVDA SpectrumX/QuantumX; the notes describe early laser shipments, roughly $50M per quarter potential in an earlier framework, and more material shipments in 2H26/1H27. Both are presented as margin-accretive, but timing is source/date dependent. | Mizuho initiation, lines 28, 203–205; LITE Q2 note, lines 35–45 and 64–69; LITE Q3 note, lines 35–59; LITE meeting note, lines 24–36. |
| 4 | Which drivers are near-term, which are medium-term, and which are still optionality? | Near-term: EML/laser shipments, 800G/1.6T transceivers, 200G-lane mix, and scale-across/DCI components. Medium-term: internal CW integration, OCS scaling, and broader 1.6T layering. Later/optionality: large-scale CPO adoption, especially beyond initial NVDA/back-end applications. The answer should use the dates in the documents rather than treat all ramps as simultaneous. | Q3 presentation, lines 40–52; Q3 note, lines 31–39 and 55–59; Jefferies report, lines 98–102; Mizuho initiation, lines 203–205. |
| 5 | The reports disagree on CPO timing. Reconcile the views instead of choosing one. | Jefferies is cautious: small CPO/LPO/LRO volumes for the next couple of years, back-end/scale-up first, and front-end adoption only gradually and potentially late in the decade. Mizuho and later LITE materials are more optimistic about NVDA-related CPO laser shipments and a meaningful 2026–27 ramp. The apparent conflict partly reflects different CPO locations/use cases—back-end/scale-up versus front-end networking—and different report dates. | Jefferies report, lines 98–102; Mizuho initiation, lines 28, 104, and 205; LITE MS fireside note, lines 24–41. |
| 6 | If CW lasers gain share from EMLs, which parts of the LITE thesis strengthen and which weaken? | Internal CW could improve transceiver margins and reduce external dependence, strengthening LITE's systems economics. But faster CW adoption could weaken EML pricing power, reduce the duration of the EML shortage, and pressure the laser-revenue/multiple thesis. InP capacity is shared between EML and CW, so the transition can also create a near-term supply-allocation tradeoff. | Morgan Stanley pricing report, lines 81–88 and 456–460; Q3 note, lines 55–61; Jefferies report, lines 56–57 and 104. |

**Set 2 follow-up behavior:** Turn 5 is a source-conflict test. A response that gives one CPO date without distinguishing use case and report date should be marked incomplete.

---

## Set 3 — Capacity, supply constraints, customer concentration, and pricing power

Primary sources: LITE notes, JPMorgan TMC conference takeaways, Morgan Stanley
pricing report, and the Bloomberg capacity note.

| # | Question | Expected answer points | Expected answer chunks |
|---|---|---|---|
| 1 | Map LITE's capacity plan by product and facility. What is the bottleneck? | The Japanese fabs are fully occupied by EML/transceiver demand; San Jose is allocated heavily or exclusively to CPO in the conference notes; the UK site is shifting toward CPO; a fifth fab is contemplated. Laser capacity was expected to rise roughly 40% through 1H26, OCS lines were described as scaling from 2 to 14 through CY26, and OCS capacity was expected to more than double by 2027-end. The bottleneck is chiefly InP/laser capacity and associated supply-chain constraints, not a lack of demand. | LITE MS fireside note, lines 36–41; LITE Q2 note, lines 55–67; JPM TMC report, lines 32–35 and 69–72; Bloomberg capacity note, lines 61–83 and 103–119. |
| 2 | What evidence supports the claim that demand exceeds supply? | EML is sold out/fully allocated through CY26; reported undershipment worsened from 20% to 25% to 30% in successive notes; customers are signing LTAs through CY27; the CEO said capacity could be sold out through 2028 within two quarters and described the agreements as non-cancelable. The answer should distinguish backlog/commitment evidence from a generic AI-demand claim. | LITE Q2 note, lines 62–67; LITE 1Q note, lines 69–73; LITE Q3 note, lines 55–61; Bloomberg capacity note, lines 34–49 and 137–144. |
| 3 | Who are the important customers, and where is the concentration risk? | Google is repeatedly identified as the primary driver of early transceiver and OCS ramps, with two other CSP customers contributing from smaller bases. NVDA is the central CPO/ELS customer in the notes and Mizuho thesis. Meta is linked to DCI/scale-across in the meeting notes. This concentration creates upside from large programs but downside if AI capex, one customer, or a design decision changes. | LITE 1Q note, lines 59–73; LITE Q2 note, lines 37–45 and 58–69; LITE meeting note, lines 26–35; Mizuho initiation, lines 118–126. |
| 4 | How does scarcity translate into pricing power? | LITE can raise prices on constrained EMLs, pump lasers, and DCI products; it can negotiate LTAs including prepay/take-or-pay terms; the resulting pricing and mix helped gross margin beat expectations. The mechanism is strongest while demand exceeds supply and customers value assured capacity. | Morgan Stanley pricing report, lines 20–29 and 79–82; LITE Q3 note, lines 55–61; LITE 1Q note, lines 69–73. |
| 5 | What would make the supply constraint stop helping LITE? | A faster capacity build, demand slowdown, AI-capex digestion, EML/CW substitution, Chinese or incumbent competition, customer inventory correction, or a shift from scarcity to equilibrium could reduce pricing. The answer should also mention that reallocating InP from EML to internal CW can relieve one bottleneck while tightening another. | Morgan Stanley pricing report, lines 84–90 and 501–514; Mizuho initiation, lines 120–126; Jefferies report, lines 56–57 and 104. |
| 6 | Build a quarterly monitoring dashboard that can distinguish demand weakness from supply-limited growth. | Demand indicators: customer backlog, LTAs/take-or-pay, customer attach rates, 1.6T/OCS/CPO orders, and hyperscaler capex. Supply indicators: EML/CW output, InP allocation, factory lines, undershipment, lead times, and contract-manufacturing capacity. Financial indicators: ASP/pricing, product mix, Components versus Systems, CloudLight/transceiver GM, and OCS/CPO revenue. The answer should say which direction each metric would move under demand weakness versus supply shortage. | JPM TMC report, lines 30–35 and 69–72; Morgan Stanley pricing report, lines 81–88; Q3 release, lines 27–29 and 47–63; Bloomberg capacity note, lines 70–83 and 103–119. |

**Set 3 follow-up behavior:** Turn 6 should use facts raised in turns 1–5, not produce a generic semiconductor dashboard.

---

## Set 4 — Bull/base/bear valuation and what would falsify the thesis

Primary sources: Mizuho initiation and Morgan Stanley's May 2026 risk/reward
report, with operating evidence from company materials.

| # | Question | Expected answer points | Expected answer chunks |
|---|---|---|---|
| 1 | Walk through Mizuho's base, bull, and bear cases for LITE. What changes between them? | Mizuho base case: $290 PT at 33x F27E P/E, assuming successful EML/CPO/OCS execution and an expanding hyperscaler pipeline. Bull case: $420 at 38x F27E P/E, requiring pipeline expansion plus competitors failing to qualify/ramp. Bear case: $180 at 25x F27E P/E, if LITE fails to win next-generation hyperscaler projects while competitors take share. | Mizuho initiation, lines 100–126. |
| 2 | Compare that framework with Morgan Stanley's later risk/reward framework. Are the price targets directly comparable? | Morgan Stanley's May 2026 framework is $900 base at roughly 30x CY28 EPS of ~$30, $1,125 bull at roughly 25x CY28 bull EPS of ~$45, and $500 bear at 20x CY28 EPS of ~$25. They are not directly comparable to Mizuho's $290/$420/$180 because they use different dates, fiscal/calendar conventions, earnings bases, share prices, and market expectations. The common operating variables are OCS/CPO execution, EML pricing/constraints, CPO adoption, and AI demand. | Morgan Stanley pricing report, lines 92–96; risk-reward section, lines 389–460; Mizuho initiation, lines 106–116. |
| 3 | Why does Mizuho justify a premium multiple for LITE? Quantify the peer comparison. | Mizuho shows LITE at 33.6x C26E P/E versus peer average 24.7x and median 25.0x. LITE's C26E EPS growth is 84% versus peer average 44% and median 40%. The premium is therefore tied to growth and strategic positioning, not simply a claim that LITE is a higher-quality company. | Mizuho initiation, lines 30–34 and 185–195. |
| 4 | What exactly must go right for the Morgan Stanley bull case? | Multiple business lines must ramp concurrently; CPO adoption in scale-up/scale-out must exceed early expectations; pricing pressure must remain favorable through CY28; EML, telecom/DCI, transceivers, OCS, and CPO must all contribute. The bull case is not just a higher multiple on the same earnings. | Morgan Stanley risk-reward, lines 419–435; pricing report, lines 96 and 503–507. |
| 5 | What are the concrete paths to the bear case? | OCS/CPO ramps miss timing, EML supply-demand equilibrates early and pricing falls, CW takes EML share, CloudLight/transceiver mix limits margin expansion, AI demand slows, or LITE fails to win cloud-provider share. These are operating mechanisms, not merely “valuation risk.” | Morgan Stanley risk-reward, lines 454–460 and 509–514; Mizuho initiation, lines 114–126; Jefferies report, lines 664–675. |
| 6 | Design a falsification checklist for the bull thesis. Which three or four data points would make you downgrade it first? | A strong answer should prioritize: failure to convert OCS/CPO backlog into shipments; EML/CW pricing or shortage normalization; slowing 1.6T/customer attach; customer concentration or AI-capex cuts; and margin deterioration as lower-margin transceivers grow. It should tie each indicator to the specific bull-case assumption it tests. | Morgan Stanley pricing report, lines 84–90 and 501–514; LITE Q3 note, lines 55–67; Mizuho initiation, lines 110–126. |

**Set 4 follow-up behavior:** The system should preserve the distinction between a change in earnings power and a change in valuation multiple. It should also keep Mizuho's F27E and Morgan Stanley's CY28 labels intact.

---

## Set 5 — Cross-company and cross-source optical-market test

Primary sources: Jefferies COHR/LITE report, the datacenter-connectivity
opportunity report, Mizuho's peer table, and LITE company materials.

| # | Question | Expected answer points | Expected answer chunks |
|---|---|---|---|
| 1 | Compare LITE and Coherent in the 200G-per-lane transition. Who benefits and why? | Jefferies views 200G/lane as positive for LITE because EML is suited to 200G+ and LITE is one of a small number of EML producers. COHR is more exposed to VCSEL, where 200G viability is debated, but it has a material and growing EML/SiPho business and should remain competitive. The correct answer should not claim COHR is eliminated. | Jefferies report, lines 56–57 and 98–104. |
| 2 | Compare LITE, Innolight, Eoptolink, and Coherent in datacenter connectivity. | The datacenter report says Innolight currently has the highest datacenter market share through volume, followed by Coherent; Eoptolink is growing and closing the gap; LITE has high share in non-datacom revenue and is pivoting toward AI/datacom. Google Palomar and LITE have an early lead in OCS, while Palomar's deployment is described as proprietary/internal. | Datacenter Connectivity Opportunity Report, lines 102–120. |
| 3 | What does the market-size data imply about the opportunity, and what does it not imply about LITE's revenue? | The connectivity market is estimated at $57.4B by 2027; transceivers at $56.34B by 2027 with 800G+ making up most of the TAM; OCS is a smaller roughly $1.06B 2027 TAM. This establishes a large market opportunity, not LITE's share or revenue. LITE-specific revenue must come from company/analyst estimates, not by assigning the whole TAM to LITE. | Datacenter Connectivity Opportunity Report, lines 23–45; optical-transceivers file, lines 17–84; Mizuho initiation, lines 201–205. |
| 4 | Reconcile the market view that CPO may be late with LITE's CPO upside case. | Jefferies expects front-end CPO adoption to be gradual and potentially late-decade, while seeing back-end/scale-up as the earlier opportunity. LITE/Mizuho materials describe earlier NVDA-related high-power laser shipments and a 2026–27 opportunity. The right synthesis is that near-term CPO can add laser/ELS content in selected systems even if broad front-end transceiver displacement remains years away. | Jefferies report, lines 98–102; Mizuho initiation, lines 203–205; LITE MS fireside note, lines 26–41. |
| 5 | Is LITE best understood as a transceiver company, a laser company, or an OCS/CPO company? Give an answer that changes over time. | Historically and structurally it is a broad optical/photonic supplier with major Components and Systems segments. The current earnings engine is EML/laser, DCI/scale-across, and CloudLight/transceivers. The forward option value is OCS and CPO, but those do not yet replace the existing engine in the reported period. | Mizuho initiation, lines 230–240; Q3 presentation, lines 40–66; LITE Q3 note, lines 31–47. |
| 6 | Write a neutral investment conclusion using at least three different source types. Separate facts, analyst estimates, and risks. | The answer should cite at least one company source, one sell-side report, and one internal/market note. It should state the demonstrated earnings/margin momentum, the EML/1.6T/OCS/CPO growth path, capacity/customer concentration, and the main bear risks. It should flag the CPO timing disagreement and avoid presenting a price target as a fact. | Company Q3 release, lines 20–29 and 47–75; Mizuho initiation, lines 104–126 and 201–218; Jefferies report, lines 98–104; Morgan Stanley risk-reward, lines 419–460. |

**Set 5 follow-up behavior:** Turns 2–4 test cross-file retrieval. Turn 6 tests source hierarchy and whether the system can distinguish company-reported facts from analyst estimates and opinions.

---

## Evaluation notes

- Grade expected content, not exact wording. A correct answer may use equivalent terminology.
- Require units, periods, and source dates for every numerical claim.
- Penalize blending FY26/FY27, fiscal-year and calendar-year estimates, or company actuals with sell-side estimates without labeling them.
- Reward answers that state when a source is an estimate, an internal note, a company filing, or a sell-side view.
- For follow-ups, check whether the answer carries forward the company/topic without needing the user to restate it.
- The materials contain duplicated note content and later revisions. A good answer should prefer the most recent directly relevant company source for actual results, then use dated notes and research to explain expectations and interpretation.
