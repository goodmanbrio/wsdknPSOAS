# Mac Studio Infrastructure Setup — PMS1/PUS Pipeline

## What we're doing (preamble for anyone reading this)

We are setting up a dedicated Mac Studio (~$10K, M4 Ultra 192GB RAM, 4TB
SSD) in the office to run an internal financial data extraction pipeline.
The machine needs to:
1. Read ~6GB of financial research documents from a SharePoint Online
   shared folder (periodic sync to local disk, not real-time)
2. Send document content to external cloud AI/LLM APIs for processing
   (Anthropic, OpenAI, DeepSeek, Google)
3. Write extracted/structured results to local disk only (no write-back
   to SharePoint for now)

The Mac is not a company-issued device. It is not domain-joined or
Intune-enrolled (unless we do so as part of this setup). It will sit
on the office network.

---

## The 6 gates

| # | Gate | If blocked | Who unblocks |
|---|------|-----------|--------------|
| 1 | Device Trust (Conditional Access) | Mac gets 0 files from SharePoint. Total blocker. | ECI |
| 2 | DLP / Sensitivity Labels | Files download encrypted or blocked. Pipeline can't read them. | ECI + Compliance |
| 3 | Network (firewall/proxy/VLAN) | Mac can't reach SharePoint, or can't reach external APIs, or neither. | ECI |
| 4 | Auth (app registration vs human login) | Sync works only when babysitting it. Breaks unattended/cron. | ECI |
| 5 | Outbound Egress to LLM APIs | Pipeline reads files fine but can't do AI inference. Dead. | ECI |
| 6 | Compliance (firm data to third-party APIs) | Legal/regulatory exposure. Policy block, not technical. | Compliance |

Gate 1 is the master gate. If no, everything else is moot.

---

## LIST A — Questions for Compliance (5-10, plain English)

Context to give them: "We want to set up a dedicated in-office computer
to run an internal research automation tool. It reads documents from our
SharePoint shared folder, processes them using external AI services
(similar to ChatGPT/Claude), and saves results locally on the machine.
No data is written back to the shared drive. Here are our questions:"

1. Is it approved for an in-office, non-company-issued computer to access
   and download documents from our SharePoint shared folder for internal
   processing purposes?

2. Is it approved for content from our internal research documents to be
   sent to external AI/LLM service providers (Anthropic, OpenAI,
   DeepSeek, Google) via their cloud APIs for automated analysis? The PM
   is aware and has approved the use case.

3. Do any of the documents in [shared folder name] carry sensitivity
   labels or data classification that would restrict downloading them
   to a non-standard device?

4. Do we need to formally register this machine as an approved device
   with our IT provider (ECI)? If so, what is the process?

5. Are there any data handling requirements for the extracted results
   stored locally on this machine? (e.g., encryption at rest, access
   controls, retention policies)

6. Do we need a written data processing agreement or vendor assessment
   for the AI service providers we'll be using (Anthropic, OpenAI,
   DeepSeek, Google)?

7. If we later want to upload AI-generated research summaries back to the
   shared drive, is there a review/approval process for that?

8. Are there any regulatory constraints (SEC, internal policies) we
   should be aware of regarding automated processing of our research
   documents?

---

## LIST B — Questions for ECI Help Portal (exhaustive, technical)

Fire these individually or in batches into the chat portal. Organized
by gate.

### Gate 1: Device Trust / Conditional Access

1.  What Conditional Access policies are currently applied to our M365
    tenant for SharePoint Online access?
2.  Do these policies require device compliance (Intune enrollment) or
    domain join to access SharePoint?
3.  Can a non-Intune, non-domain-joined macOS device download files from
    SharePoint Online under current policies?
4.  If not: can you create a Conditional Access exclusion for a specific
    device or service principal?
5.  If not: can you enroll a macOS device (Apple Silicon Mac Studio) in
    Intune? What is the process?
6.  Does our tenant use Entra ID (Azure AD) Conditional Access "compliant
    device" or "hybrid Azure AD joined" as a grant control?
7.  Is there a Named Location policy that would allow access from the
    office IP range without device compliance?

### Gate 2: DLP / Data Loss Prevention

8.  Is Microsoft Purview Information Protection configured on our tenant?
9.  Are there sensitivity labels applied to files in our SharePoint
    document libraries?
10. If yes: which labels, and do any of them enforce download restrictions
    or encryption?
11. Are there DLP policies that block file download to unmanaged devices
    or external apps?
12. Would an Entra ID app registration (service principal) with
    Files.Read.All be subject to these DLP policies?
13. Can you check if the specific shared folder [folder name/URL] has
    any label or DLP policy applied?

### Gate 3: Network

14. What is the network topology for our office? (VLAN segmentation,
    corporate vs guest networks)
15. Which network segment should a new device connect to if it needs
    both M365/SharePoint access AND outbound internet access?
16. Is there a web proxy or firewall appliance filtering outbound HTTPS
    traffic?
17. If yes: is it a transparent proxy or does it require explicit proxy
    configuration on the device?
18. Does the proxy/firewall do TLS/SSL inspection (MITM decryption)?
19. If TLS inspection: can specific domains be exempted? (API endpoints
    break if their TLS certs are rewritten)
20. Is outbound HTTPS (port 443) to arbitrary external domains allowed
    by default, or is it whitelist-only?
21. Does the device need a static IP assignment or MAC address
    reservation?
22. Is there a DNS configuration we need to use (internal DNS servers)?

### Gate 4: Authentication / App Registration

23. Can you create an Entra ID (Azure AD) app registration in our
    tenant for automated, unattended access to SharePoint files?
24. The app needs Microsoft Graph API permission: Files.Read.All
    (application-level, not delegated) with admin consent.
25. Can you provide: tenant ID, client ID, client secret (or
    certificate) for this app registration?
26. What is the expiry policy for client secrets in our tenant? Can it
    be set to 12+ months?
27. Is there a process for rotating the client secret when it expires?
28. As an alternative to app registration: can a service account (non-
    human M365 user) be created for this purpose with its own
    credentials and MFA exemption?
29. If using a service account: can it be excluded from Conditional
    Access MFA requirements?

### Gate 5: Outbound Egress to Specific Domains

30. Can you whitelist the following external domains for outbound HTTPS
    from the Mac Studio's IP / network segment?
    - api.anthropic.com
    - api.openai.com
    - api.deepseek.com
    - generativelanguage.googleapis.com
    - login.microsoftonline.com (M365 auth)
    - graph.microsoft.com (SharePoint API)
    - *.sharepoint.com (SharePoint file access)
31. If TLS inspection is active: can these domains be added to a bypass
    list so their certificates are not rewritten?
32. Are there any outbound data transfer limits or bandwidth throttling
    policies?
33. Is there an outbound proxy that requires authentication? If yes,
    what auth method (NTLM, basic, Kerberos)?

### Gate 6: General / Miscellaneous

34. What is the process for adding a new non-Windows device to the
    office network?
35. Do we need to install any ECI management agent / endpoint protection
    software on the Mac?
36. Is there a required antivirus/EDR solution for devices on the
    corporate network?
37. What is the SLA / turnaround time for the changes we're requesting?
38. Who is our primary ECI contact / account manager for change requests?
39. Is there a change management / approval process on ECI's side for
    these modifications?
40. Can you provide documentation for our tenant's current security
    baseline / policy configuration?

---

## EMAIL VERSION — Meeting Agenda / Pre-Read

Subject: Infrastructure setup request — dedicated Mac for internal
research automation

Hi [ECI contact / team],

We'd like to set up a dedicated Apple Mac Studio in our office to run
an internal financial research automation tool. Below is what the
machine needs to do and the questions we need answered.

**What the machine does:**
- Downloads ~6GB of documents from a specific SharePoint Online shared
  folder (periodic automated sync, not real-time)
- Sends document content to external cloud AI APIs (Anthropic, OpenAI,
  DeepSeek, Google) for processing
- Saves processed results to its own local storage (no write-back to
  SharePoint)

**The machine:**
- Apple Mac Studio (macOS, Apple Silicon)
- Will sit in our office on the company network
- Not currently domain-joined or Intune-enrolled

**What we need from ECI:**

1. **Device access to SharePoint:** Can this machine access and download
   files from SharePoint Online under our current Conditional Access
   policies? If not, what do we need to change — Intune enrollment,
   policy exception, or something else?

2. **Automated sync (app registration):** Can you create an Entra ID
   app registration with Files.Read.All (application, admin-consented)
   so the machine can sync files without interactive human login? We
   need tenant ID, client ID, and client secret.

3. **DLP / sensitivity labels:** Are there any DLP policies or
   sensitivity labels on our SharePoint files that would block
   downloads to this device or deliver encrypted files?

4. **Network access:** Which network segment should this device connect
   to? Does outbound HTTPS to external domains require whitelisting?
   Domains we need:
   - api.anthropic.com
   - api.openai.com
   - api.deepseek.com
   - generativelanguage.googleapis.com

5. **TLS inspection:** If there's a proxy doing TLS inspection, can the
   above domains be exempted? (API clients break on rewritten certs)

6. **Endpoint requirements:** Do we need to install any ECI management
   agents, antivirus, or EDR on this Mac?

7. **Timeline:** What's the turnaround for these changes?

Happy to jump on a call to discuss. We can also answer any questions
about the use case.

Thanks,
[Your name]

---

## Spec (purchase decision, not for ECI)

- M4 Ultra, 192GB unified memory (local model inference option later)
- 4TB SSD (6GB sync now, room for growth + pipeline artifacts)
- Thunderbolt external storage if ever needed for 30TB

## Later (not this meeting)

- Analyst-facing LAN access: Gradio UI 0.0.0.0, auth/queueing unsolved
- Write-back to SharePoint: manual for now, compliance conversation later
- Operational handoff docs: app registration creds, rclone config, API keys, cron
