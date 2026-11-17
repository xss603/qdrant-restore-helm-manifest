# Kubernetes Egress Guide: Allowing Pod Traffic to SharePoint Online

## 1. Overview & DNS Resolution Behavior

Allowing Kubernetes pods in a restricted namespace to communicate with SharePoint Online (`*.sharepoint.com` / `apps.sharepoint.com`) over **TCP port 443** requires accounting for Microsoft's Anycast routing and multi-hop CNAME chains.

### Typical DNS Resolution Chain
When a pod resolves a SharePoint endpoint (e.g., `apps.sharepoint.com` or `<tenant>.sharepoint.com`), DNS traverses multiple intermediate CNAME domains before reaching the final edge IP addresses:

```text
apps.sharepoint.com
  └── *.clump.dprodmgd104.aa-rt.sharepoint.com
        └── *.farm.dprodmgd104.sharepointonline.com.akadns.net   (Akamai GTM)
              └── dual-spo-0005.spo-msedge.net                   (Microsoft Edge)
                    ├── 13.107.136.10
                    └── 13.107.138.10
```

> **Important:** Different `*.sharepoint.com` subdomains do **not** always resolve to the same CIDR block. While `13.107.136.10` and `13.107.138.10` fall within `13.107.136.0/22`, regional failover, Anycast geo-routing, and shared service subdomains (`apps.sharepoint.com`, `*.svc.ms`, `static.sharepointonline.com`) pull from multiple Microsoft `AS8075` CIDR prefixes.

---

## 2. Official SharePoint Online CIDRs (TCP 443)

To prevent intermittent connection timeouts during regional failovers or DNS rotations, include the full set of Microsoft 365 SharePoint Online / OneDrive CIDR prefixes rather than a single `/22` block:

| CIDR Block | IP Range | Role / Notes |
| :--- | :--- | :--- |
| `13.107.136.0/22` | `13.107.136.0` – `13.107.139.255` | Primary SharePoint Anycast Edge (`spo-msedge.net`) |
| `40.108.128.0/17` | `40.108.128.0` – `40.108.255.255` | SharePoint Online & OneDrive regional pools |
| `52.104.0.0/14` | `52.104.0.0` – `52.107.255.255` | Microsoft 365 / SharePoint front-end services |
| `104.146.128.0/17` | `104.146.128.0` – `104.146.255.255` | SharePoint Online regional edge |
| `150.171.40.0/22` | `150.171.40.0` – `150.171.43.255` | SharePoint Online edge services |

### Fetching Up-to-Date CIDRs via Microsoft's Official API
Microsoft updates M365 IP ranges monthly. You can query the live list for the `SharePoint` service area using the official Microsoft 365 IP Address and URL Web Service:

```bash
curl -s "https://endpoints.office.com/endpoints/worldwide?clientrequestid=$(uuidgen)" \
  | jq -r '.[] | select(.serviceArea=="SharePoint" and .ips != null) | .ips[]' \
  | grep -v ':' \
  | sort -u
```

*(Note: If your workload authenticates via OAuth2/Entra ID before calling SharePoint APIs, you must also permit egress to `login.microsoftonline.com` on TCP 443.)*

---

## 3. Policy Implementation by Enforcement Engine

### Option A: Illumio Core (Labels + IP Lists)
Illumio enforces Layer 3/4 rules against the final resolved destination IP (`13.107.136.10`, etc.), so the intermediate CNAMEs (`akadns.net`, `spo-msedge.net`) do not require separate rules.

1. **Map Namespace Labels to Illumio Labels:**
   In the Illumio PCE (**Infrastructure → Container Clusters → Namespace Mapping**), map your Kubernetes namespace label (e.g., `app: sharepoint-client`, `env: prod`) to Illumio's **Role / Application / Environment / Location** labels.
2. **Create an IP List (`M365-SharePoint-Online`):**
   Navigate to **Policy Objects → IP Lists → Add** and insert the SharePoint CIDR blocks listed in Section 2 (`13.107.136.0/22`, `40.108.128.0/17`, `52.104.0.0/14`, `104.146.128.0/17`, `150.171.40.0/22`).
3. **Create the Outbound Ruleset:**
   * **Consumers (Source):** Your mapped Illumio workload/namespace labels
   * **Providers (Destination):** `M365-SharePoint-Online` IP List
   * **Service:** `TCP 443`
---

## 4. Verification & Troubleshooting

1. **Test DNS and TCP 443 Connectivity from a Pod:**
   ```bash
   kubectl exec -n your-labeled-namespace <pod-name> -- \
     curl -I -s -o /dev/null -w "%{http_code}\n" https://apps.sharepoint.com
   ```
   * **Expected Result:** HTTP `200`, `302`, or `403` (confirms TLS handshake succeeded; `403` simply indicates an unauthenticated request at the application layer).
   * **Failure (`000` / Timeout):** Egress traffic is still blocked by a firewall, Illumio policy, or missing DNS rule.

2. **Illumio Traffic Verification:**
   * Inspect **Illumination Plus / Traffic** in the Illumio PCE UI, filtering by your namespace labels and destination port `443` to confirm flows to `13.107.136.0/22` appear as **Allowed**.

---

## 5. Official Microsoft References

* **SharePoint Online & OneDrive URLs and IP Address Ranges:**  
  [Microsoft 365 URLs and IP address ranges (IDs 31–46)](https://learn.microsoft.com/en-us/microsoft-365/enterprise/urls-and-ip-address-ranges?view=o365-worldwide#sharepoint-online-and-onedrive-for-business)
* **Microsoft 365 IP Address and URL Web Service (REST API):**  
  [Microsoft 365 IP Address and URL Web Service Documentation](https://learn.microsoft.com/en-us/microsoft-365/enterprise/microsoft-365-ip-web-service?view=o365-worldwide)
* **Managing Microsoft 365 Endpoints & Firewall Rules:**  
  [Managing Microsoft 365 Endpoints](https://learn.microsoft.com/en-us/microsoft-365/enterprise/managing-office-365-endpoints?view=o365-worldwide)
* **Microsoft Entra ID (Authentication Endpoints):**  
  [Microsoft 365 Common and Office Online Endpoints](https://learn.microsoft.com/en-us/microsoft-365/enterprise/urls-and-ip-address-ranges?view=o365-worldwide#microsoft-365-common-and-office-online)
