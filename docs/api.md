## API

[Provisioning endpoints](#provisioning-endpoints)
- [POST /api/provision/users](#post-apiprovisionusers): Create a user and its API key.
- [POST /api/provision/users/:user_id/api_key](#post-apiprovisionusersuser_idapi_key): Replace a user's API key.
- [PATCH /api/provision/users/:user_id](#patch-apiprovisionusersuser_id): Set the account state (subscription stage).
- [DELETE /api/provision/users/:user_id](#delete-apiprovisionusersuser_id): Delete a user.
- [POST /api/provision/aliases/:alias_id/transfer](#post-apiprovisionaliasesalias_idtransfer): Give an alias to another user.

[Account endpoints](#account-endpoints)
- [GET /api/user_info](#get-apiuser_info): Get user's information.
- [PATCH /api/user_info](#patch-apiuser_info): Update user's information.
- [GET /api/stats](#get-apistats): Get user's stats.

[Alias endpoints](#alias-endpoints)
- [GET /api/v5/alias/options](#get-apiv5aliasoptions): Get alias options. Used by create alias process.
- [POST /api/v3/alias/custom/new](#post-apiv3aliascustomnew): Create new alias.
- [POST /api/alias/random/new](#post-apialiasrandomnew): Random an alias.
- [GET /api/v2/aliases](#get-apiv2aliases): Get user's aliases.
- [GET /api/aliases/:alias_id](#get-apialiasesalias_id): Get alias information.
- [DELETE /api/aliases/:alias_id](#delete-apialiasesalias_id): Delete an alias.
- [POST /api/aliases/:alias_id/toggle](#post-apialiasesalias_idtoggle): Enable/disable an alias.
- [GET /api/aliases/:alias_id/activities](#get-apialiasesalias_idactivities): Get alias activities.
- [PATCH /api/aliases/:alias_id](#patch-apialiasesalias_id): Update alias information.
- [GET /api/aliases/:alias_id/contacts](#get-apialiasesalias_idcontacts): Get alias contacts.
- [POST /api/aliases/:alias_id/contacts](#post-apialiasesalias_idcontacts): Create a new contact for an alias.

[Mailbox endpoints](#mailbox-endpoints)
- [POST /api/mailboxes](#post-apimailboxes): Create a new mailbox.
- [POST /api/mailboxes/:mailbox_id/verify](#post-apimailboxesmailbox_idverify): Confirm a mailbox or a mailbox email change.
- [DELETE /api/mailboxes/:mailbox_id](#delete-apimailboxesmailbox_id): Delete a mailbox.
- [PUT /api/mailboxes/:mailbox_id](#put-apimailboxesmailbox_id): Update a mailbox.

[Custom domain endpoints](#custom-domain-endpoints)
- [GET /api/custom_domains](#get-apicustom_domains): Get custom domains.
- [PATCH /api/custom_domains/:custom_domain_id](#patch-apicustom_domainscustom_domain_id): Update custom domain's information.
- [GET /api/custom_domains/:custom_domain_id/trash](#get-apicustom_domainscustom_domain_idtrash): Get deleted aliases of a custom domain.
- [POST /api/custom_domains](#post-apicustom_domains): Add a custom domain.
- [GET /api/custom_domains/:custom_domain_id/dns](#get-apicustom_domainscustom_domain_iddns): DNS records to set up.
- [POST /api/custom_domains/:custom_domain_id/dns](#post-apicustom_domainscustom_domain_iddns): Check the DNS records.
- [DELETE /api/custom_domains/:custom_domain_id](#delete-apicustom_domainscustom_domain_id): Delete a custom domain.

[Directory endpoints](#directory-endpoints)
- [GET /api/directories](#get-apidirectories), [POST /api/directories](#post-apidirectories),
  [PATCH /api/directories/:directory_id](#patch-apidirectoriesdirectory_id),
  [DELETE /api/directories/:directory_id](#delete-apidirectoriesdirectory_id)

[Contact endpoints](#contact-endpoints)
- [DELETE /api/contacts/:contact_id](#delete-apicontactscontact_id): Delete a contact.
- [POST /api/contacts/:contact_id/toggle](#post-apicontactscontact_idtoggle): Block/unblock a contact.

[Notification endpoints](#notification-endpoints)
- [GET /api/notifications](#get-apinotifications): Get notifications.
- [POST /api/notifications/:notification_id](#post-apinotificationsnotification_id): Mark as read a notification.

[Settings endpoints](#settings-endpoints)
- [GET /api/setting](#get-apisetting): Get user's settings.
- [PATCH /api/setting](#patch-apisetting): Update user's settings.
- [GET /api/v2/setting/domains](#get-apiv2settingdomains): Get domains that user can use to create random alias.

[Import and export endpoints](#import-and-export-endpoints)
- [GET /api/export/data](#get-apiexportdata): Export user's data.
- [GET /api/export/aliases](#get-apiexportaliases): Export aliases into a CSV.

---

This service has a single API client: the backend in front of it, which owns accounts, login and billing.
Each account of that backend has its own user here, created through the provisioning endpoints, and its own
API key. Every other endpoint is authenticated with that key in the `Authentication` header and only gives
access to the data of its user. There are no sessions, cookies, passwords or login endpoints.

For some endpoints, the `hostname` should be passed in query string. `hostname` is the the URL hostname (
cf https://en.wikipedia.org/wiki/URL), for ex if URL is http://www.example.com/index.html then the hostname
is `www.example.com`. This information is important to know where an alias is used in order to suggest user the same
alias if they want to create on alias on the same website in the future.

If error, the API returns 4** with body containing the error message, for example:

```json
{
  "error": "request body cannot be empty"
}
```

The error message could be displayed to user as-is, for example for when user exceeds their alias quota. Some errors
should be fixed during development however: for example error like `request body cannot be empty` is there to catch
development error and should never be shown to user.

All following endpoint return `401` status code if the API Key is incorrect.

### Provisioning endpoints

These endpoints are authenticated with the provisioning key set in `PROVISIONING_API_KEY`, sent in the
`X-Provisioning-Key` header. They return 404 when `PROVISIONING_API_KEY` is not set, and 401 when the key is
missing or wrong. They are rate limited by `PROVISIONING_RATE_LIMIT` (default `100/minute`) per source IP.

#### POST /api/provision/users

Create a user and its API key. The user's email is a random placeholder on `PROVISIONED_USER_EMAIL_DOMAIN`
(default `users.invalid`); no email is ever sent to it.

Output: 201

```json
{"user_id": 42, "api_key": "a long string"}
```

The API key is only returned here and by the rotate endpoint: store it.

#### POST /api/provision/users/:user_id/api_key

Revoke every API key of the user and create a new one.

Output: 200 `{"api_key": "a long string"}`, 404 if the user does not exist or is being deleted.

#### PATCH /api/provision/users/:user_id

Set the account state from its subscription. Input: `state`:

- `active`: everything works;
- `grace`: mail to existing aliases is forwarded, but replies are refused (550, "Replies are disabled for this
  account") and no alias is created (API, catch-all, directories);
- `paused`: mail to the user's aliases and replies are refused with a temporary error (421), so senders retry and
  mail is delivered if the account becomes active again; nothing is created.

Mail to an address that would create an alias on the fly (catch-all, directory) gets a temporary error (421) in
`grace` and `paused`. `POST /api/provision/users` also accepts `state` (default `active`). The state is shown as
`account_state` in `GET /api/user_info`, and `can_create` of `GET /api/v5/alias/options` is false outside `active`.

Output: 200 `{"user_id": 42, "state": "grace"}`, 400 for an unknown state, 404 if the user does not exist or is
being deleted.

#### DELETE /api/provision/users/:user_id

Delete a user with its aliases, mailboxes and contacts. Its API keys are revoked and its aliases stop handling
mail immediately; the data is deleted by the job runner.

Output: 200 `{"ok": true}`, 404 if the user does not exist or is already being deleted.

#### POST /api/provision/aliases/:alias_id/transfer

Give an alias, with its contacts, to another user. This backend decides who may transfer what (for example
after the receiving account accepted a transfer link).

Input (body):

- `to_user_id`: the receiving user
- `mailbox_ids`: verified mailboxes of the receiving user that the alias will forward to

Output: 200 `{"ok": true}`; 404 if the alias or the user does not exist; 400 if the alias is on a custom domain,
in a directory or in the trash, or if the mailboxes are not verified mailboxes of the receiving user.
The previous owner gets a notification.

### Account endpoints

#### GET /api/user_info

Return the user's information. This endpoint can be used to validate an API key.

Output:

```json
{
  "name": "",
  "is_premium": true,
  "email": "0f3b...@users.invalid",
  "can_create_reverse_alias": true
}
```

#### PATCH /api/user_info

Input:

- name

Output: same as GET /api/user_info

#### GET /api/stats

Return stats about the number of aliases, number of emails forwarded/replied/blocked

```json
{"nb_alias": 1, "nb_block": 0, "nb_forward": 0, "nb_reply": 0}
```

### Alias endpoints

#### GET /api/v5/alias/options

User alias info and suggestion. Used by the first extension screen when user opens the extension.

Input:

- `Authentication` header that contains the api key
- (Optional but recommended) `hostname` passed in query string.

Output: a json with the following field:

- can_create: boolean. Whether user can create new alias
- suffixes: list of alias suffix that user can use.
  Each item is a dictionary with `suffix`, `signed-suffix`, `is_custom`, `is_premium` as keys.
  The `signed-suffix` is necessary to avoid request tampering.
- prefix_suggestion: string. Suggestion for the `alias prefix`. Usually this is the website name extracted
  from `hostname`. If no `hostname`, then the `prefix_suggestion` is empty.
- recommendation: optional field, dictionary. If an alias is already used for this website, the recommendation will be
  returned. There are 2 subfields in `recommendation`: `alias` which is the recommended alias and `hostname` is the
  website on which this alias is used before.

For ex:

```json
{
  "can_create": true,
  "prefix_suggestion": "test",
  "suffixes": [
    {
      "signed_suffix": ".cat@d1.test.X6_7OQ.0e9NbZHE_bQvuAapT6NdBml9m6Q",
      "suffix": ".cat@d1.test",
      "is_custom": true,
      "is_premium": false
    },
    {
      "signed_suffix": ".chat@d2.test.X6_7OQ.TTgCrfqPj7UmlY723YsDTHhkess",
      "suffix": ".chat@d2.test",
      "is_custom": false,
      "is_premium": false
    },
    {
      "signed_suffix": ".yeah@sl.lan.X6_7OQ.i8XL4xsMsn7dxDEWU8eF-Zap0qo",
      "suffix": ".yeah@sl.lan",
      "is_custom": true,
      "is_premium": false
    }
  ]
}
```

#### POST /api/v3/alias/custom/new

Create a new custom alias.

Input:

- `Authentication` header that contains the api key
- (Optional but recommended) `hostname` passed in query string
- Request Message Body in json (`Content-Type` is `application/json`)
    - alias_prefix: string. The first part of the alias that user can choose.
    - signed_suffix: should be one of the suffixes returned in the `GET /api/v5/alias/options` endpoint.
    - mailbox_ids: list of mailbox_id that "owns" this alias
    - (Optional) note: alias note
    - (Optional) name: alias name

Output:
If success, 201 with the new alias info. Use the same format as in GET /api/aliases/:alias_id

#### POST /api/alias/random/new

Create a new random alias.

Input:

- `Authentication` header that contains the api key
- (Optional but recommended) `hostname` passed in query string
- (Optional) mode: either `uuid` or `word` passed in query string. By default, use the user setting when creating new random alias.
- Request Message Body in json (`Content-Type` is `application/json`)
    - (Optional) note: alias note

Output:
If success, 201 with the new alias info. Use the same format as in GET /api/aliases/:alias_id

#### GET /api/v2/aliases

Get user aliases.

Input:

- `Authentication` header that contains the api key
- `page_id` in query. Used for the pagination. The endpoint returns maximum 20 aliases for each page. `page_id` starts
  at 0.
- (Optional) `pinned` in query. If set, only pinned aliases are returned.
- (Optional) `disabled` in query. If set, only disabled aliases are returned.
- (Optional) `enabled` in query. If set, only enabled aliases are returned.
  Please note `pinned`, `disabled`, `enabled` are exclusive, i.e. only one can be present.
- (Optional) query: included in request body. Some frameworks might prevent GET request having a non-empty body, in this
  case this endpoint also supports POST.

Output:
If success, 200 with the list of aliases. Each alias has the following fields:

- id
- email
- name
- enabled
- creation_timestamp
- note
- nb_block
- nb_forward
- nb_reply
- support_pgp: whether an alias can support PGP, i.e. when one of alias's mailboxes supports PGP.
- disable_pgp: whether the PGP is disabled on this alias. This field should only be used when `support_pgp` is true. By
  setting `disable_pgp=true`, a user can explicitly disable PGP on an alias even its mailboxes support PGP.
- mailbox: obsolete, should use `mailboxes` instead.
    - id
    - email
- mailboxes: list of mailbox, contains at least 1 mailbox.
    - id
    - email
- (nullable) latest_activity:
    - action: forward|reply|block|bounced
    - timestamp
    - contact:
        - email
        - name
        - reverse_alias
- pinned: whether an alias is pinned

Here's an example:

```json
{
  "aliases": [
    {
      "creation_date": "2020-04-06 17:57:14+00:00",
      "creation_timestamp": 1586195834,
      "email": "prefix1.cat@sl.lan",
      "name": "A Name",
      "enabled": true,
      "id": 3,
      "mailbox": {
        "email": "a@b.c",
        "id": 1
      },
      "mailboxes": [
        {
          "email": "m1@cd.ef",
          "id": 2
        },
        {
          "email": "john@wick.com",
          "id": 1
        }
      ],
      "latest_activity": {
        "action": "forward",
        "contact": {
          "email": "c1@example.com",
          "name": null,
          "reverse_alias": "\"c1 at example.com\" <re1@SL>"
        },
        "timestamp": 1586195834
      },
      "nb_block": 0,
      "nb_forward": 1,
      "nb_reply": 0,
      "note": null,
      "pinned": true
    }
  ]
}
```

#### GET /api/aliases/:alias_id

Get alias info

Input:

- `Authentication` header that contains the api key
- `alias_id` in url

Output:
Alias info, use the same format as in /api/v2/aliases. For example:

```json
{
  "creation_date": "2020-04-06 17:57:14+00:00",
  "creation_timestamp": 1586195834,
  "email": "prefix1.cat@sl.lan",
  "name": "A Name",
  "enabled": true,
  "id": 3,
  "mailbox": {
    "email": "a@b.c",
    "id": 1
  },
  "mailboxes": [
    {
      "email": "m1@cd.ef",
      "id": 2
    },
    {
      "email": "john@wick.com",
      "id": 1
    }
  ],
  "latest_activity": {
    "action": "forward",
    "contact": {
      "email": "c1@example.com",
      "name": null,
      "reverse_alias": "\"c1 at example.com\" <re1@SL>"
    },
    "timestamp": 1586195834
  },
  "nb_block": 0,
  "nb_forward": 1,
  "nb_reply": 0,
  "note": null,
  "pinned": true
}
```

#### DELETE /api/aliases/:alias_id

Delete an alias

Input:

- `Authentication` header that contains the api key
- `alias_id` in url.

Output:
If success, 200.

```json
{
  "deleted": true
}
```

#### POST /api/aliases/:alias_id/toggle

Enable/disable alias

Input:

- `Authentication` header that contains the api key
- `alias_id` in url.

Output:
If success, 200 along with the new alias status:

```json
{
  "enabled": false
}
```

#### GET /api/aliases/:alias_id/activities

Get activities for a given alias.

Input:

- `Authentication` header that contains the api key
- `alias_id`: the alias id, passed in url.
- `page_id` used in request query (`?page_id=0`). The endpoint returns maximum 20 aliases for each page. `page_id`
  starts at 0.

Output:
If success, 200 with the list of activities, for example:

```json
{
  "activities": [
    {
      "action": "reply",
      "from": "yes_meo_chat@sl.lan",
      "timestamp": 1580903760,
      "to": "marketing@example.com",
      "reverse_alias": "\"marketing at example.com\" <reply@a.b>",
      "reverse_alias_address": "reply@a.b"
    }
  ]
}
```

#### PATCH /api/aliases/:alias_id

Update alias info.

Input:

- `Authentication` header that contains the api key
- `alias_id` in url.
- (optional) `note` in request body
- (optional) `mailbox_id` in request body
- (optional) `name` in request body
- (optional) `mailbox_ids` in request body: array of mailbox_id
- (optional) `disable_pgp` in request body: boolean
- (optional) `pinned` in request body: boolean

Output:
If success, return 200

#### GET /api/aliases/:alias_id/contacts

Get contacts for a given alias.

Input:

- `Authentication` header that contains the api key
- `alias_id`: the alias id, passed in url.
- `page_id` used in request query (`?page_id=0`). The endpoint returns maximum 20 contacts for each page. `page_id`
  starts at 0.

Output:
If success, 200 with the list of contacts, for example:

```json
{
  "contacts": [
    {
      "id": 1,
      "contact": "marketing@example.com",
      "creation_date": "2020-02-21 11:35:00+00:00",
      "creation_timestamp": 1582284900,
      "last_email_sent_date": null,
      "last_email_sent_timestamp": null,
      "reverse_alias": "marketing at example.com <reply+bzvpazcdedcgcpztehxzgjgzmxskqa@sl.co>",
      "block_forward": false
    },
    {
      "id": 2,
      "contact": "newsletter@example.com",
      "creation_date": "2020-02-21 11:35:00+00:00",
      "creation_timestamp": 1582284900,
      "last_email_sent_date": "2020-02-21 11:35:00+00:00",
      "last_email_sent_timestamp": 1582284900,
      "reverse_alias": "newsletter at example.com <reply+bzvpazcdedcgcpztehxzgjgzmxskqa@sl.co>",
      "reverse_alias_address": "reply+bzvpazcdedcgcpztehxzgjgzmxskqa@sl.co",
      "block_forward": true
    }
  ]
}
```

Please note that last_email_sent_timestamp and last_email_sent_date can be null.

#### POST /api/aliases/:alias_id/contacts

Create a new contact for an alias.

Input:

- `Authentication` header that contains the api key
- `alias_id` in url.
- `contact` in request body

Output:
If success, return 201.

Return 200 and `existed=true` if contact is already added.

```json
{
  "id": 1,
  "contact": "First Last <first@example.com>",
  "creation_date": "2020-03-14 11:52:41+00:00",
  "creation_timestamp": 1584186761,
  "last_email_sent_date": null,
  "last_email_sent_timestamp": null,
  "reverse_alias": "First Last first@example.com <ra+qytyzjhrumrreuszrbjxqjlkh@sl.lan>",
  "reverse_alias_address": "reply+bzvpazcdedcgcpztehxzgjgzmxskqa@sl.co",
  "existed": false
}
```

It can return 403 with an error if the user cannot create reverse alias.

```json
{
  "error": "Please upgrade to create a reverse-alias"
}
```

### Mailbox endpoints

#### GET /api/v2/mailboxes

Get user's mailboxes, including unverified ones.

Input:

- `Authentication` header that contains the api key

Output:
List of mailboxes. Each mailbox has id, email, default, creation_timestamp field

```json
{
  "mailboxes": [
    {
      "email": "a@b.c",
      "id": 1,
      "default": true,
      "creation_timestamp": 1590918512,
      "nb_alias": 10,
      "verified": true
    },
    {
      "email": "m1@example.com",
      "id": 2,
      "default": false,
      "creation_timestamp": 1590918512,
      "nb_alias": 0,
      "verified": false
    }
  ]
}
```

## Mailbox endpoints
#### POST /api/mailboxes

Create a new mailbox

Input:

- `Authentication` header that contains the api key
- email: the new mailbox address

Output:

- 201 along with the following response if new mailbox is created successfully. User is going to receive a verification
  email.
    - id: integer
    - email: the mailbox email address
    - verified: boolean.
    - default: whether is the default mailbox. User cannot delete the default mailbox
- 400 with error message otherwise. The error message can be displayed to user.

#### POST /api/mailboxes/:mailbox_id/verify

Confirm a new mailbox, or a pending change of its address (PUT with `email`), with the code sent to the
mailbox. When `MAILBOX_VERIFY_URL` is set, the verification email links to
`MAILBOX_VERIFY_URL?mailbox_id=<id>&code=<code>`; that page calls this endpoint. Otherwise the email only
contains the code. Codes expire after 15 minutes and after 3 wrong attempts.

Input:

- `Authentication` header that contains the api key
- code: in body

Output:

- 200 with the mailbox (same fields as above)
- 400 with an error message if the code is wrong, expired or the mailbox is not the user's

#### DELETE /api/mailboxes/:mailbox_id

Delete a mailbox. User cannot delete the default mailbox

Input:

- `Authentication` header that contains the api key
- `mailbox_id`: in url
- (optional) `transfer_aliases_to`: in body as json. id of the new mailbox for the aliases. If omitted or set to -1, the aliases will be delete with the mailbox.

Output:

- 200 if deleted successfully
- 400 if error

#### PUT /api/mailboxes/:mailbox_id

Update a mailbox.

Input:

- `Authentication` header that contains the api key
- `mailbox_id`: in url
- (optional) `default`: boolean. Set a mailbox as default mailbox.
- (optional) `email`: email address. Change a mailbox email address.
- (optional) `cancel_email_change`: boolean. Cancel mailbox email change.
- (optional) `pgp_public_key`: armored PGP public key used to encrypt forwarded emails; `null` or `""` removes it.
- (optional) `disable_pgp`: boolean. Keep the key but stop encrypting.
- (optional) `generic_subject`: string of at most 78 characters that replaces the subject of forwarded emails
  (hides it from the mailbox provider); `null` turns it off.

Mailbox objects returned by the API also contain `new_email` (pending address change waiting for
`POST /api/mailboxes/:mailbox_id/verify`, or `null`), `pgp_fingerprint`, `disable_pgp` and `generic_subject`.

Output:

- 200 if updated successfully
- 400 if error

### Custom domain endpoints

#### GET /api/custom_domains

Return user's custom domains

Input:

- `Authentication` header that contains the api key

Output:
List of custom domains.

```json
[
  {
    "catch_all": false,
    "creation_date": "2021-03-10 21:36:08+00:00",
    "creation_timestamp": 1615412168,
    "domain_name": "test1.org",
    "id": 1,
    "is_verified": true,
    "mailboxes": [
      {
        "email": "a@b.c",
        "id": 1
      }
    ],
    "name": null,
    "nb_alias": 0,
    "random_prefix_generation": false
  },
  {
    "catch_all": false,
    "creation_date": "2021-03-10 21:36:08+00:00",
    "creation_timestamp": 1615412168,
    "domain_name": "test2.org",
    "id": 2,
    "is_verified": false,
    "mailboxes": [
      {
        "email": "a@b.c",
        "id": 1
      }
    ],
    "name": null,
    "nb_alias": 0,
    "random_prefix_generation": false
  }
]
```

#### PATCH /api/custom_domains/:custom_domain_id

Update custom domain's information

Input:

- `Authentication` header that contains the api key
- `custom_domain_id` in url.
- (optional) `catch_all`: boolean, in request body
- (optional) `random_prefix_generation`: boolean, in request body
- (optional) `name`: text, in request body
- (optional) `mailbox_ids`: array of mailbox id, in request body

Output:
If success, return 200 along with updated custom domain

#### GET /api/custom_domains/:custom_domain_id/trash

Get deleted alias for a custom domain

Input:

- `Authentication` header that contains the api key

Output:
List of deleted alias.

```json
{
  "aliases": [
    {
      "alias": "first@test1.org",
      "deletion_timestamp": 1605464595
    }
  ]
}
```

#### POST /api/custom_domains

Add a custom domain. Input: `domain`. Output: 201 with the custom domain (same fields as GET, plus
`ownership_verified`, `spf_verified`, `dkim_verified`, `dmarc_verified`, `pending_deletion`), or 400 with an error.
Aliases can only be created on the domain once its ownership is verified; it receives mail once its MX records
point to this service. `catch_all` (PATCH) creates aliases on the fly for any address of the domain.

#### GET /api/custom_domains/:custom_domain_id/dns

The DNS records to set up, and whether each is verified:

```json
{
  "ownership": {"verified": false, "type": "TXT", "host": "example.com", "value": "sl-verification=..."},
  "mx": {"verified": false, "records": [{"priority": 10, "value": "mx1.example.net."}]},
  "spf": {"verified": false, "type": "TXT", "host": "example.com", "value": "v=spf1 include:... ~all"},
  "dkim": {"verified": false, "records": [{"type": "CNAME", "host": "dkim._domainkey.example.com", "value": "..."}]},
  "dmarc": {"verified": false, "type": "TXT", "host": "_dmarc.example.com", "value": "v=DMARC1; ..."}
}
```

#### POST /api/custom_domains/:custom_domain_id/dns

Look the records up now and update what is verified. Input (optional): `check`, one of `ownership`, `mx`, `spf`,
`dkim`, `dmarc`; all of them when omitted. The other records are only checked once ownership is verified
(checking one of them before is a 400). Output: the same as GET plus `errors`, `{check: [records found]}` for each
failed check.

#### DELETE /api/custom_domains/:custom_domain_id

Delete the domain and its aliases (done by the job runner). Output: 200 `{"deleted": true}`.

### Directory endpoints

A directory `name` creates aliases on the fly for `name+anything@` / `name/anything@` on the shared alias domains.
Names are global, 3 to 32 characters (`a-z 0-9 - _`), and cannot be reused after deletion.

- `GET /api/directories`: `{"directories": [{"id", "name", "disabled", "nb_alias", "creation_timestamp", "mailboxes": [{"id", "email"}]}]}`
- `POST /api/directories`: `name`, `mailbox_ids` (verified mailboxes, required). 201 with the directory.
- `PATCH /api/directories/:directory_id`: optional `disabled` (no more aliases created on the fly), `mailbox_ids`.
- `DELETE /api/directories/:directory_id`: deletes the directory and its aliases.

When a provisioned user verifies its first mailbox, it replaces the placeholder default mailbox, so mail that
falls back to the default mailbox (random aliases without mailboxes, catch-all) reaches a real mailbox.

### Contact endpoints

#### DELETE /api/contacts/:contact_id

Delete a contact

Input:

- `Authentication` header that contains the api key
- `contact_id` in url.

Output:
If success, 200.

```json
{
  "deleted": true
}
```

#### POST /api/contacts/:contact_id/toggle

Block/unblock contact

Input:

- `Authentication` header that contains the api key
- `contact_id` in url.

Output:
If success, 200 along with the new alias status:

```json
{
  "block_forward": false
}
```

### Notification endpoints

#### GET /api/notifications

Get notifications. Users have no email of their own, so everything addressed to the account (quarantined
emails, disabled aliases, bounces, custom domain problems, deletions...) is stored here for the frontend to
show; emails are only sent to the user's mailboxes. `message` is HTML. Each notification also has
`created_at_timestamp` (unix time) besides the human readable `created_at`.

Input:

- `Authentication` in header: the api key
- page in url: the page number, starts at 0

Output:

- more: whether there's more notifications
- notifications: list of notification, each notification has:
    - id
    - message: the message in html
    - title: the message title
    - read: whether the user has read the notification
    - created_at: when the notification is created

For example

```json
{
  "more": false,
  "notifications": [
    {
      "created_at": "2 minutes ago",
      "id": 1,
      "message": "Hey!",
      "read": false
    }
  ]
}
```

#### POST /api/notifications/:notification_id

Mark a notification as read

Input:

- `Authentication` in header: the api key
- notification_id in url: the page number, starts at 0

Output:
200 if success

### Settings endpoints

#### GET /api/setting

Return user setting.

```json
{
  "alias_generator": "word",
  "notification": true,
  "random_alias_default_domain": "sl.lan",
  "sender_format": "AT",
  "random_alias_suffix": "random_string"
}
```

#### PATCH /api/setting

Update user setting. All input fields are optional.

Input:

- alias_generator (string): `uuid` or `word`
- random_alias_default_domain (string): one of the domains returned by `GET /api/setting/domains`
- sender_format (string): possible values are `AT`, `A`, `NAME_ONLY`, `AT_ONLY`, `NO_NAME`
- random_alias_suffix (string): possible values are `word`, `random_string`
- unsubscribe_behaviour (string): what the "Unsubscribe" button of the mail client does on forwarded emails:
  `disable_alias`, `block_contact` (block the sender) or `preserve_original` (the sender's own unsubscribe link)

Output: same as `GET /api/setting`

#### GET /api/v2/setting/domains

Return domains that user can use to create random alias

`is_custom` is true if this is a user's domain, otherwise false.

```json
[
  {
    "domain": "d1.test",
    "is_custom": false
  },
  {
    "domain": "d2.test",
    "is_custom": false
  },
  {
    "domain": "sl.lan",
    "is_custom": false
  },
  {
    "domain": "ab.cd",
    "is_custom": true
  }
]
```

### Import and export endpoints

#### GET /api/export/data

Export user data

Input:

- `Authentication` in header: the api key

Output:
Alias, custom domain and app info

#### GET /api/export/aliases

Export user aliases in an importable CSV format

Input:

- `Authentication` in header: the api key

Output:
A CSV file with alias information that can be imported in the settings screen
