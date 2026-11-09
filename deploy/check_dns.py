"""Prints the DNS records the alias domain needs and checks what is published.

Run by init.sh on each start; never fails the start, only reports.
"""

import re

import dns.resolver
import dns.reversename

from app.config import (
    DKIM_SELECTOR,
    DKIM_SELECTORS,
    EMAIL_DOMAIN,
    EMAIL_SERVERS_WITH_PRIORITY,
    NOREPLY,
)

resolver = dns.resolver.Resolver(configure=False)
resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
resolver.lifetime = 5

mail_host = EMAIL_SERVERS_WITH_PRIORITY[0][1].rstrip(".")
SIGNING_SELECTOR = DKIM_SELECTOR.decode()
DKIM_KEYS = list(DKIM_SELECTORS)
with open("/sl/dkim.pub.key") as f:
    dkim_pub = "".join(line.strip() for line in f if "-----" not in line)

expected = {
    f"A     {mail_host}": "<this server's IPv4 address>",
    "PTR   <server IP>": f"{mail_host}  (set at the hosting provider)",
    f"MX    {EMAIL_DOMAIN}": f"10 {mail_host}.",
    f"TXT   {EMAIL_DOMAIN}": "v=spf1 mx -all",
    # custom domains CNAME all three selectors here; the server signs with DKIM_SELECTOR
    **{
        f"TXT   {key}._domainkey.{EMAIL_DOMAIN}": f"v=DKIM1; k=rsa; p={dkim_pub}"
        for key in DKIM_KEYS
    },
    f"TXT   _dmarc.{EMAIL_DOMAIN}": "v=DMARC1; p=quarantine; adkim=s; aspf=s",
}
# Service emails (mailbox confirmation, alerts) are sent from NOREPLY. On another domain they
# only need the DKIM key there: bounces and SPF stay on the alias domain.
NOREPLY_DOMAIN = NOREPLY.split("@", 1)[1].lower()
if NOREPLY_DOMAIN != EMAIL_DOMAIN:
    expected[
        f"TXT   {SIGNING_SELECTOR}._domainkey.{NOREPLY_DOMAIN}"
    ] = f"v=DKIM1; k=rsa; p={dkim_pub}  (service emails from {NOREPLY})"
print("DNS records for mail:")
for name, value in expected.items():
    print(f"  {name}  ->  {value}")
print("  (custom domains get theirs from GET /api/custom_domains/<id>/dns)")


def lookup(name, rtype):
    try:
        return [r.to_text() for r in resolver.resolve(name, rtype)]
    except Exception:
        return []


def txt(name):
    return ["".join(re.findall(r'"([^"]*)"', r)) for r in lookup(name, "TXT")]


problems = []
addresses = lookup(mail_host, "A")
if not addresses:
    problems.append(f"{mail_host} has no A record")
for ip in addresses:
    ptr = [p.rstrip(".") for p in lookup(dns.reversename.from_address(ip), "PTR")]
    if mail_host not in ptr:
        problems.append(f"PTR of {ip} is {ptr or 'missing'}, should be {mail_host}")
if lookup(mail_host, "AAAA"):
    problems.append(
        f"{mail_host} has an AAAA record; Postfix only uses IPv4, remove it"
    )
mx = [r.split()[-1].rstrip(".") for r in lookup(EMAIL_DOMAIN, "MX")]
if mail_host not in mx:
    problems.append(f"MX of {EMAIL_DOMAIN} is {mx or 'missing'}, should be {mail_host}")
if not [r for r in txt(EMAIL_DOMAIN) if r.startswith("v=spf1")]:
    problems.append(f"{EMAIL_DOMAIN} has no SPF record")
for key in DKIM_KEYS:
    published = txt(f"{key}._domainkey.{EMAIL_DOMAIN}")
    if key == SIGNING_SELECTOR:
        if not [r for r in published if dkim_pub in r.replace(" ", "")]:
            problems.append(
                f"{key}._domainkey.{EMAIL_DOMAIN} (DKIM_SELECTOR) does not publish this "
                "server's DKIM key: signed mail fails DKIM"
            )
    elif not [r for r in published if r.startswith("v=DKIM1")]:
        problems.append(f"{key}._domainkey.{EMAIL_DOMAIN} has no DKIM key")
if NOREPLY_DOMAIN != EMAIL_DOMAIN and not [
    r
    for r in txt(f"{SIGNING_SELECTOR}._domainkey.{NOREPLY_DOMAIN}")
    if dkim_pub in r.replace(" ", "")
]:
    problems.append(
        f"{SIGNING_SELECTOR}._domainkey.{NOREPLY_DOMAIN} does not publish this server's DKIM "
        f"key: service emails from {NOREPLY} fail DKIM and DMARC"
    )
if not [r for r in txt(f"_dmarc.{EMAIL_DOMAIN}") if r.startswith("v=DMARC1")]:
    problems.append(f"_dmarc.{EMAIL_DOMAIN} has no DMARC record")

for problem in problems:
    print(f"WARNING: {problem}")
if not problems:
    print("DNS for mail: OK")
