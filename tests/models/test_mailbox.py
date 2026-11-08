from app.dns_utils import set_global_dns_client, InMemoryDNSClient

dns_client = InMemoryDNSClient()


def setup_module():
    set_global_dns_client(dns_client)


def teardown_module():
    set_global_dns_client(None)
