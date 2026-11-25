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

Thunderbird handles this in three ways, depending on what each source offers.

**1. Email mailing lists**

Subscribing happens outside Thunderbird. You sign up with your email address, and the messages arrive in your inbox.

- **Web form (Mailman lists):** Open the list's info page, enter your address, and confirm. Examples are Full Disclosure (nmap.org/mailman/listinfo/fulldisclosure) and Ubuntu (ubuntu-security-announce on lists.ubuntu.com).
- **Email-based:** oss-security lists subscription instructions on openwall.com/lists, and Debian has a form at lists.debian.org. Kernel lists (vger.kernel.org) use their own subscription instructions on their info page.
- **Confirmation:** Most lists send a confirmation email. Reply to it or click its link, or you won't be subscribed.

**2. RSS/Atom feeds, built into Thunderbird**

This suits CISA, CERT-In, SANS ISC, vendor advisories, news sites, and GitHub advisories.

1. Open **Account Settings** (the menu at the top right, or right-click your account).
2. Click **Account Actions → Add Feed Account**.
3. Select the new **Feeds** account and click **Manage Subscriptions**.
4. Paste the feed URL, click **Add**, then **OK**.

To find a feed URL, look for the RSS icon or an "RSS/Subscribe" link on the advisory page. Thunderbird updates feeds automatically, and each feed appears as a folder, so you get a separate reading area from your mail.

**3. Newsgroup access to kernel lists**

For Linux kernel lists you can skip subscribing. lore.kernel.org offers NNTP access, which Thunderbird supports:

1. **Account Actions → Add Other Account → Newsgroup Account**.
2. Use `nntp.lore.kernel.org` as the server.
3. Right-click the account, choose **Subscribe**, and tick the lists you want (for example `org.kernel.vger.netdev`).

This keeps the high-volume lists out of your inbox.

**Keeping it manageable**

- Right-click a message and choose **Create Filter from Message** to auto-sort each list into its own folder.
- Use a separate email address or alias for list subscriptions, since some lists are noisy.
- Mark high-volume lists as read automatically with a filter, and only check them when needed.

If you tell me which sources you want first, I can give the exact subscription addresses and feed URLs for those.

Gmail doesn't have built-in RSS, so the approach splits: mailing lists work the same way, and feeds need a workaround.

**1. Email mailing lists**

Subscribe exactly as before (web form or confirmation email) using your Gmail address. The only Gmail-specific work is organizing the incoming mail:

1. Open one of the list emails.
2. Click the three-dot menu → **Filter messages like these**.
3. Match on the **To** or **List-Id** header (for example, put the list address in the "To" or "Has the words" field, such as `list:oss-security.lists.openwall.com`).
4. Click **Create filter**, then choose **Apply the label** (create a new one like "Security/oss-security"), and optionally **Skip the Inbox** and **Never send it to Spam**.

Check the **Spam** folder after subscribing, since confirmation emails sometimes land there.

**2. RSS/Atom feeds (CISA, CERT-In, SANS, vendors, GitHub)**

Gmail can't read feeds directly, so use a feed-to-email service:

- **Blogtrottr** (blogtrottr.com): paste a feed URL, give your email, and choose instant or digest delivery. Easiest option.
- **Feedrabbit** or **Follow.it**: similar services, with digest options.
- **IFTTT or Zapier**: more flexible; you can trigger an email per new feed item, but free tiers are limited.

Alternatively, use a dedicated feed reader (Feedly, Inoreader, NetNewsWire) and skip email for those sources. Many people prefer this because feeds can be high-volume.

**3. Newsletters (Krebs, tl;dr sec, Risky Business)**

Just subscribe with your Gmail address on their sites; they arrive as regular email.

**Keeping Gmail manageable**

- Use a label per source and filter them out of the Inbox, then check the labels on a schedule.
- Use a **plus alias** like `yourname+security@gmail.com` when subscribing. Gmail delivers it to your inbox, and you can filter on the "To" address easily.
- Kernel lists are very high volume, so for those read lore.kernel.org directly or use Thunderbird's NNTP access rather than flooding Gmail.

If you'd like, I can give you the exact subscription links and feed URLs for a starter set (oss-security, CISA, CERT-In, Microsoft, and so on).
