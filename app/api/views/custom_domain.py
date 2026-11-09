from flask import g
from flask import jsonify

from app.api.base import (
    api_bp,
    is_optional_str,
    json_body,
    parse_int_list,
    require_api_auth,
)
from app import config
from app.constants import DMARC_RECORD
from app.custom_domain_utils import (
    create_custom_domain,
    delete_custom_domain,
    set_custom_domain_mailboxes,
)
from app.custom_domain_validation import CustomDomainValidation
from app.db import Session
from app.dns_utils import get_network_dns_client
from app.extensions import limiter
from app.log import LOG
from app.models import CustomDomain, DomainDeletedAlias


def custom_domain_to_dict(custom_domain: CustomDomain):
    return {
        "id": custom_domain.id,
        "domain_name": custom_domain.domain,
        "is_verified": custom_domain.verified,
        "nb_alias": custom_domain.nb_alias(),
        "creation_date": custom_domain.created_at.format(),
        "creation_timestamp": custom_domain.created_at.timestamp,
        "catch_all": custom_domain.catch_all,
        "name": custom_domain.name,
        "random_prefix_generation": custom_domain.random_prefix_generation,
        "mailboxes": [
            {"id": mb.id, "email": mb.email} for mb in custom_domain.mailboxes
        ],
        "ownership_verified": custom_domain.ownership_verified,
        "spf_verified": custom_domain.spf_verified,
        "dkim_verified": custom_domain.dkim_verified,
        "dmarc_verified": custom_domain.dmarc_verified,
        "pending_deletion": custom_domain.pending_deletion,
    }


@api_bp.route("/custom_domains", methods=["GET"])
@require_api_auth
def get_custom_domains():
    user = g.user
    custom_domains = CustomDomain.filter_by(
        user_id=user.id, is_sl_subdomain=False
    ).all()

    return jsonify(custom_domains=[custom_domain_to_dict(cd) for cd in custom_domains])


@api_bp.route("/custom_domains/<int:custom_domain_id>/trash", methods=["GET"])
@require_api_auth
def get_custom_domain_trash(custom_domain_id: int):
    user = g.user
    custom_domain = CustomDomain.get(custom_domain_id)
    if not custom_domain or custom_domain.user_id != user.id:
        return jsonify(error="Forbidden"), 403

    domain_deleted_aliases = DomainDeletedAlias.filter_by(
        domain_id=custom_domain.id
    ).all()

    return jsonify(
        aliases=[
            {
                "alias": dda.email,
                "deletion_timestamp": dda.created_at.timestamp,
            }
            for dda in domain_deleted_aliases
        ]
    )


@api_bp.route("/custom_domains/<int:custom_domain_id>", methods=["PATCH"])
@require_api_auth
@limiter.limit("100/hour")
def update_custom_domain(custom_domain_id):
    """
    Update alias note
    Input:
        custom_domain_id: in url
    In body:
        catch_all (optional): boolean
        random_prefix_generation (optional): boolean
        name (optional): in body
        mailbox_ids (optional): array of mailbox_id
    Output:
        200
    """
    data = json_body()
    if not data:
        return jsonify(error="request body cannot be empty"), 400

    user = g.user
    custom_domain: CustomDomain = CustomDomain.get(custom_domain_id)

    if not custom_domain or custom_domain.user_id != user.id:
        return jsonify(error="Forbidden"), 403

    changed = False
    if "catch_all" in data:
        catch_all = data.get("catch_all")
        if not isinstance(catch_all, bool):
            return jsonify(error="Invalid catch_all"), 400
        custom_domain.catch_all = catch_all
        changed = True

    if "random_prefix_generation" in data:
        random_prefix_generation = data.get("random_prefix_generation")
        if not isinstance(random_prefix_generation, bool):
            return jsonify(error="Invalid random_prefix_generation"), 400
        custom_domain.random_prefix_generation = random_prefix_generation
        changed = True

    if "name" in data:
        name = data.get("name")
        if not is_optional_str(name) or (name and len(name) > 128):
            return jsonify(error="Invalid name"), 400
        custom_domain.name = name
        changed = True

    if "mailbox_ids" in data:
        mailbox_ids = parse_int_list(data.get("mailbox_ids"))
        if mailbox_ids is None:
            return jsonify(error="Invalid mailbox_ids"), 400
        result = set_custom_domain_mailboxes(user.id, custom_domain, mailbox_ids)
        if result.success:
            changed = True
        else:
            LOG.info(
                f"Prevented from updating mailboxes [custom_domain_id={custom_domain.id}]: {result.reason.value}"
            )
            return jsonify(error="Forbidden"), 400

    if changed:
        Session.commit()

    # refresh
    custom_domain = CustomDomain.get(custom_domain_id)
    return jsonify(custom_domain=custom_domain_to_dict(custom_domain)), 200


def _own_domain(custom_domain_id: int):
    custom_domain = CustomDomain.get(custom_domain_id)
    if (
        not custom_domain
        or custom_domain.user_id != g.user.id
        or custom_domain.is_sl_subdomain
    ):
        return None
    return custom_domain


@api_bp.route("/custom_domains", methods=["POST"])
@limiter.limit("10/hour")
@require_api_auth
def create_custom_domain_route():
    """
    Add a custom domain. It can be used for aliases once its ownership is verified
    (TXT record) and receives mail once its MX records point to this service; see
    GET/POST /api/custom_domains/<id>/dns.
    Input: domain
    Output: 201 and the custom domain, 400 with an error message
    """
    user = g.user
    if not user.can_create_new_alias():
        return jsonify(error="This account cannot create new domains right now"), 400
    domain = json_body().get("domain")
    if not isinstance(domain, str) or not domain.strip():
        return jsonify(error="Invalid domain"), 400

    result = create_custom_domain(user, domain)
    if not result.success:
        return jsonify(error=result.message), 400
    Session.commit()
    return jsonify(custom_domain_to_dict(result.instance)), 201


def _dns_state(custom_domain: CustomDomain, validator: CustomDomainValidation) -> dict:
    ownership = validator.get_ownership_verification_record(custom_domain)
    return {
        "ownership": {
            "verified": custom_domain.ownership_verified,
            "type": "TXT",
            "host": custom_domain.domain,
            "value": ownership.recommended,
        },
        "mx": {
            "verified": custom_domain.verified,
            "records": [
                {"priority": priority, "value": records.recommended}
                for priority, records in sorted(
                    validator.get_expected_mx_records(custom_domain).items()
                )
            ],
        },
        "spf": {
            "verified": custom_domain.spf_verified,
            "type": "TXT",
            "host": custom_domain.domain,
            "value": validator.get_expected_spf_record(custom_domain),
        },
        "dkim": {
            "verified": custom_domain.dkim_verified,
            "records": [
                {
                    "type": "CNAME",
                    "host": f"{key}.{custom_domain.domain}",
                    "value": records.recommended,
                }
                for key, records in validator.get_dkim_records(custom_domain).items()
            ],
        },
        "dmarc": {
            "verified": custom_domain.dmarc_verified,
            "type": "TXT",
            "host": f"_dmarc.{custom_domain.domain}",
            "value": DMARC_RECORD,
        },
    }


@api_bp.route("/custom_domains/<int:custom_domain_id>/dns", methods=["GET"])
@require_api_auth
def get_custom_domain_dns(custom_domain_id: int):
    """DNS records to set up for the domain, with what is verified so far."""
    custom_domain = _own_domain(custom_domain_id)
    if not custom_domain:
        return jsonify(error="Forbidden"), 403
    validator = CustomDomainValidation(
        dkim_domain=config.EMAIL_DOMAIN, dns_client=get_network_dns_client()
    )
    return jsonify(_dns_state(custom_domain, validator)), 200


_DNS_CHECKS = ("ownership", "mx", "spf", "dkim", "dmarc")


@api_bp.route("/custom_domains/<int:custom_domain_id>/dns", methods=["POST"])
@limiter.limit("30/hour")
@require_api_auth
def check_custom_domain_dns(custom_domain_id: int):
    """
    Look up the domain's DNS records now and update what is verified.
    Input: check: one of ownership, mx, spf, dkim, dmarc (all of them when omitted).
        The other records are only checked once ownership is verified.
    Output: the same as GET, plus "errors": {check: [records found]} for failed checks
    """
    custom_domain = _own_domain(custom_domain_id)
    if not custom_domain:
        return jsonify(error="Forbidden"), 403
    check = json_body().get("check")
    if check is not None and check not in _DNS_CHECKS:
        return jsonify(error="Invalid check"), 400
    checks = [check] if check else list(_DNS_CHECKS)

    validator = CustomDomainValidation(
        dkim_domain=config.EMAIL_DOMAIN, dns_client=get_network_dns_client()
    )
    errors = {}
    if "ownership" in checks:
        result = validator.validate_domain_ownership(custom_domain)
        if not result.success:
            errors["ownership"] = result.errors
    if custom_domain.ownership_verified:
        if "mx" in checks:
            result = validator.validate_mx_records(custom_domain)
            if not result.success:
                errors["mx"] = result.errors
        if "spf" in checks:
            result = validator.validate_spf_records(custom_domain)
            if not result.success:
                errors["spf"] = result.errors
        if "dkim" in checks:
            invalid = validator.validate_dkim_records(custom_domain)
            if invalid:
                errors["dkim"] = [f"{host}: {value}" for host, value in invalid.items()]
        if "dmarc" in checks:
            result = validator.validate_dmarc_records(custom_domain)
            if not result.success:
                errors["dmarc"] = result.errors
    elif check and check != "ownership":
        return jsonify(error="Verify the domain ownership first"), 400

    Session.commit()
    return jsonify({**_dns_state(custom_domain, validator), "errors": errors}), 200


@api_bp.route("/custom_domains/<int:custom_domain_id>", methods=["DELETE"])
@require_api_auth
def delete_custom_domain_route(custom_domain_id: int):
    """
    Delete the domain and its aliases (done by the job runner).
    Output: 200 {"deleted": true}
    """
    custom_domain = _own_domain(custom_domain_id)
    if not custom_domain:
        return jsonify(error="Forbidden"), 403
    if not custom_domain.pending_deletion:
        LOG.i("User %s deletes %s", g.user, custom_domain)
        delete_custom_domain(custom_domain)
    return jsonify(deleted=True), 200
