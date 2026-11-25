If you mean mailing lists like **netdev** (the Linux networking subsystem list), here are the ones focused on security.

**Linux kernel specific**
- **linux-cve-announce**: the kernel CNA announces every CVE it assigns, including networking fixes. Subscribe via vger.kernel.org or read it on lore.kernel.org.
- **netdev**: the networking subsystem development list. It's where net security patches get discussed, but it's high-volume and not a dedicated security feed.
- **security@kernel.org**: for *reporting* kernel vulnerabilities privately, not for receiving updates.
- **linux-distros**: a private list for coordinated disclosure among distributions. You need to be an eligible distro security contact to join.

**General open-source security**
- **oss-security** (Openwall): the main public list for open-source vulnerability disclosures and discussion. It's the best general-purpose one.
- **Full Disclosure** (Seclists): unmoderated disclosure of vulnerabilities and exploits.
- **Bugtraq**: historically important, but largely inactive now.

**Distribution security announcements**
- Debian: `debian-security-announce`
- Ubuntu: `ubuntu-security-announce`
- Red Hat: `rhsa-announce`
- Fedora, openSUSE and Arch each have their own security announce lists or feeds.

**Government and CERT feeds**
- CISA alerts and advisories (including the Known Exploited Vulnerabilities catalog)
- CERT/CC vulnerability notes
- CERT-In, India's national CERT, which publishes advisories you may find relevant

**Tips**
- **lore.kernel.org** hosts public archives of kernel lists, so you can read or search them without subscribing. It also offers Atom feeds and `lei` for local searches.
- For a manageable inbox, start with **oss-security** plus **linux-cve-announce**, and add your distro's security list.

I can suggest how to filter or automate these if you tell me whether you're after kernel networking specifically or broader coverage.

Got it, here are security mailing lists and feeds that cover a broad range of software, vendors, and platforms.

**General vulnerability disclosure and discussion**
- **oss-security** (Openwall): open-source software across all platforms, with a lot of technical detail and coordinated disclosures.
- **Full Disclosure** (Seclists): unmoderated, covers anything, with variable quality.
- **Bugtraq**: historically the standard, now mostly dormant but archived at Seclists.
- **SANS Internet Storm Center** (isc.sans.edu): daily diary and handler posts on active threats, with email and RSS subscriptions.

**Government and national CERTs**
- **CISA**: alerts, advisories, ICS advisories, and the Known Exploited Vulnerabilities (KEV) catalog.
- **US-CERT / CERT/CC**: vulnerability notes covering multiple vendors.
- **CERT-In** (India): advisories and vulnerability notes relevant to your region.
- **NCSC UK**, **CERT-EU**, **JPCERT/CC**: good regional and international coverage, many with English feeds.
- **NVD** (NIST): CVE data feeds and email updates.

**Vendor security bulletins**
- Microsoft Security Response Center (Patch Tuesday notifications)
- Cisco, Fortinet, Palo Alto, Juniper, VMware/Broadcom, Oracle (quarterly Critical Patch Update)
- Apple, Google (Android, Chrome releases), Mozilla

Most offer email subscriptions or RSS feeds on their security advisory pages.

**Language and ecosystem advisories**
- GitHub Security Advisories (watch specific repos or ecosystems)
- npm, PyPI, RubyGems, Go, and Rust (RustSec) advisory feeds
- OSV.dev, an aggregated open-source vulnerability database with an API

**Threat intelligence and news**
- **US-CERT bulletins** and **Krebs on Security** (email subscription)
- **The Hacker News**, **BleepingComputer**, **SecurityWeek**, and **Dark Reading** for news
- **Risky Business** newsletter and **tl;dr sec** for curated weekly roundups

**Practical setup**
- Start with **oss-security**, **CISA**, **CERT-In**, and the vendor bulletins for software you actually run.
- Use RSS plus a reader, or email filters/labels, to keep volume under control.
- A "watch" on GitHub Security Advisories for your dependencies gives targeted alerts.

If you tell me what you run (cloud, enterprise software, specific vendors, development stacks), I can narrow this to a short list.
