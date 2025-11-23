#!/usr/bin/env python3
#########################################################################################################################################################################
#
# Created by: Jan Hüls "finex7070" StickyStoneStudio GmbH
# Originally created by: William Grzybowski "MacJediWizard" MacJediWizard Consulting, Inc.
#
# Script: main.py
#
# Description:
# - This FastAPI application serves as a SCIM 2.0 bridge for provisioning and managing Mailcow mailboxes.
# - Built for integration with Authentik or other SCIM-compatible identity providers.
# - Automatically provisions mailboxes using SCIM `/Users` endpoints.
# - Provides a Prometheus-compatible `/metrics` endpoint for monitoring and observability.
# - Secured with Bearer token authentication for all SCIM endpoints.
# - Fully containerized for deployment via Docker Compose or Portainer.
#
# Notes:
# - Expects environment variables for API keys and configuration.
# - All responses follow SCIM 2.0 standards where applicable.
#
# Planned:
# - Maps SCIM group memberships to mailbox tags and create and alias which sends to all members.
#
# License:
# This application is licensed under the MIT License.
# See the LICENSE file in the root of this repository for details.
#
#########################################################################################################################################################################

import os, httpx, sqlite3, uuid, json
from dotenv import load_dotenv
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

load_dotenv()

DB_PATH= os.getenv("DB_PATH", "/data/data.db")
SCIM_TOKEN = os.getenv("SCIM_TOKEN")
MAILCOW_API_URL = os.getenv("MAILCOW_API_URL")
MAILCOW_API_KEY = os.getenv("MAILCOW_API_KEY")
SKIP_VERIFY_CERTIFICATE = os.getenv("SKIP_VERIFY_CERTIFICATE", False)
ALLOW_DELETE = os.getenv("ALLOW_DELETE", True)
MAILCOW_DELETE_MAILBOX = os.getenv("MAILCOW_DELETE_MAILBOX", False)

REQUIRED_ENV_VARS = {
    "SCIM_TOKEN": SCIM_TOKEN,
    "MAILCOW_API_URL": MAILCOW_API_URL,
    "MAILCOW_API_KEY": MAILCOW_API_KEY,
}

missing = [k for k, v in REQUIRED_ENV_VARS.items() if not v]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

api = FastAPI()

# --- Database ---
dbconn = sqlite3.connect(DB_PATH)
dbcur = dbconn.cursor()

dbcur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    mailcowId TEXT,
    scimId TEXT,
    active INTEGER,
    userName TEXT UNIQUE,
    displayName TEXT,
    emails TEXT
)
""")

# dbcur.execute("""
# CREATE TABLE IF NOT EXISTS groups (
#     id TEXT PRIMARY KEY,
#     mailcowId TEXT,
#     scimId TEXT,
#     active INTEGER,
#     displayName TEXT,
#     members TEXT
# )
# """)

dbcur.execute("""
CREATE TABLE IF NOT EXISTS metrics (
    name TEXT PRIMARY KEY,
    value INTEGER
)
""")

dbcur.execute("""
    INSERT INTO metrics (name, value)
    VALUES ('users_created', 0)
    ON CONFLICT(name) DO NOTHING
""")

dbcur.execute("""
    INSERT INTO metrics (name, value)
    VALUES ('users_updated', 0)
    ON CONFLICT(name) DO NOTHING
""")

dbcur.execute("""
    INSERT INTO metrics (name, value)
    VALUES ('users_deleted', 0)
    ON CONFLICT(name) DO NOTHING
""")

dbconn.commit()
dbconn.close()

# --- Scim Models ---
class SCIMUser(BaseModel):
    schemas: list
    id: Optional[str] = None
    externalId: Optional[str] = None
    active: bool
    userName: str
    displayName: Optional[str] = None
    emails: list
    
class SCIMGroup(BaseModel):
    schemas: list
    id: Optional[str] = None
    externalId: Optional[str] = None
    displayName: str
    members: Optional[list] = None

class SCIMListResponse(BaseModel):
    schemas: list
    totalResults: int
    itemsPerPage: int
    startIndex: int
    Resources: list

# --- API ---
def get_async_client():
    if SKIP_VERIFY_CERTIFICATE:
        return httpx.AsyncClient(verify=False)
    else:
        return httpx.AsyncClient()
    
def get_metrics():
    dbconn = sqlite3.connect(DB_PATH)
    dbcur = dbconn.cursor()
    dbcur.execute("SELECT name, value FROM metrics")
    rows = dbcur.fetchall()
    metrics = []
    for name, value in rows:
        metrics.append(f"# HELP {name} SCIM metric for {name}")
        metrics.append(f"# TYPE {name} counter")
        metrics.append(f"{name} {value}")
    dbconn.close()
    return "\n".join(metrics)
    
def get_primary_mail(mails: list):
    if len(mails) < 1:
        return None
    for mail in mails:
        if mail.get("primary") is True:
            return mail.get("value")
    return mail[0].get("value")

async def create_user(user: SCIMUser):
    email = get_primary_mail(user.emails)
    if email is not None:
        dbconn = sqlite3.connect(DB_PATH)
        dbcur = dbconn.cursor()
        dbcur.execute("SELECT 1 FROM users WHERE scimId = ? OR userName = ?", (user.externalId, user.userName))
        if dbcur.fetchone():
            dbconn.close()
            raise HTTPException(status_code=409, detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "409",
                "scimType": "uniqueness",
                "detail": f"User with id '{user.externalId}' or userName '{user.userName}' already exists",
            })
        code, resp = await create_mailbox(email.split("@")[0], email.split("@")[1], user.displayName)
        if not (code == 200 and resp and resp[0]["type"] == "success"):
            dbconn.close()
            raise HTTPException(status_code=502, detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "502",
                "scimType": "serverError",
                "detail": "Request failed: upstream API returned an error",
            })
        user.id = str(uuid.uuid4())
        dbcur.execute("""
            INSERT INTO users (id, mailcowId, scimId, active, userName, displayName, emails)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            resp[0]["msg"][1],
            user.externalId,
            int(user.active),
            user.userName,
            user.displayName,
            json.dumps(user.emails)
        ))
        dbcur.execute("""
            UPDATE metrics
            SET value = value + 1
            WHERE name = 'users_created'
        """)
        dbconn.commit()
        dbconn.close()
        return user
    else:
        raise HTTPException(status_code=400, detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "400",
            "scimType": "invalidSyntax",
            "detail": "Missing required attribute: emails"
        })
    
def get_user(id: str):
    dbconn = sqlite3.connect(DB_PATH)
    dbcur = dbconn.cursor()
    dbcur.execute("""
        SELECT id, scimId, active, userName, displayName, emails
        FROM users
        WHERE id = ?
    """, (id,))
    row = dbcur.fetchone()
    if not row:
        dbconn.close()
        raise HTTPException(status_code=404, detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "404",
            "scimType": "notFound",
            "detail": f"User with id '{id}' not found"
        })
    user = SCIMUser(
        schemas=["urn:ietf:params:scim:schemas:core:2.0:User"],
        id=id,
        externalId=row[1],
        active=bool(row[2]),
        userName=row[3],
        displayName=row[4],
        emails=json.loads(row[5])
    )
    dbconn.close()
    return user

def get_users(index: int, count: int):
    dbconn = sqlite3.connect(DB_PATH)
    dbcur = dbconn.cursor()
    offset = max(index - 1, 0)
    dbcur.execute("SELECT COUNT(*) FROM users")
    total = dbcur.fetchone()[0]
    dbcur.execute("""
        SELECT id, scimId, active, userName, displayName, emails
        FROM users
        LIMIT ? OFFSET ?
    """, (count, offset))
    rows = dbcur.fetchall()
    resources: List[SCIMUser] = []
    for row in rows:
        user = SCIMUser(
            schemas=["urn:ietf:params:scim:schemas:core:2.0:User"],
            id=id,
            externalId=row[1],
            active=bool(row[2]),
            userName=row[3],
            displayName=row[4],
            emails=json.loads(row[5])
        )
        resources.append(user)
    dbconn.close()
    return SCIMListResponse(
        schemas=["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        totalResults=total,
        itemsPerPage=len(resources),
        startIndex=index,
        Resources=resources
    )

async def delete_user(id: str):
    if not ALLOW_DELETE:
        raise HTTPException(status_code=403, detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "scimType": "mutability",
            "detail": f"Deletion of user with id '{id}' is not allowed."
        })
    dbconn = sqlite3.connect(DB_PATH)
    dbcur = dbconn.cursor()
    dbcur.execute("SELECT mailcowId FROM users WHERE id = ?", (id))
    row = dbcur.fetchone()
    if not row:
        dbconn.close()
        raise HTTPException(status_code=404, detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "404",
            "scimType": "notFound",
            "detail": f"User with id '{id}' not found"
        })
    if MAILCOW_DELETE_MAILBOX:
        code, resp = await delete_mailbox(row[0])
        if not (code == 200 and resp and resp[0]["type"] == "success"):
            dbconn.close()
            raise HTTPException(status_code=502, detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "502",
                "scimType": "serverError",
                "detail": "Request failed: upstream API returned an error",
            })
        dbcur.execute("DELETE FROM users WHERE id = ?", (id))
        dbcur.execute("""
            UPDATE metrics
            SET value = value + 1
            WHERE name = 'users_deleted'
        """)
        dbconn.commit()
        dbconn.close()
    else:
        dbcur.execute("DELETE FROM users WHERE id = ?", (id))
        dbcur.execute("""
            UPDATE metrics
            SET value = value + 1
            WHERE name = 'users_deleted'
        """)
        dbconn.commit()
        dbconn.close()

async def update_user(id: str, user: SCIMUser):
    email = get_primary_mail(user.emails)
    if email is not None:
        dbconn = sqlite3.connect(DB_PATH)
        dbcur = dbconn.cursor()
        dbcur.execute("""
            SELECT mailcowId
            FROM users
            WHERE id = ?
        """, (id,))
        row = dbcur.fetchone()
        if not row:
            dbconn.close()
            raise HTTPException(status_code=404, detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "404",
                "scimType": "notFound",
                "detail": f"User with id '{id}' not found"
            })
        mailcow_id = row[0]
        code, resp = await update_mailbox(mailcow_id, user.active, user.displayName)
        if not (code == 200 and resp and resp[0]["type"] == "success"):
            dbconn.close()
            raise HTTPException(status_code=502, detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "502",
                "scimType": "serverError",
                "detail": "Request failed: upstream API returned an error",
            })
        if email != mailcow_id:
            code, resp = await rename_mailbox(mailcow_id, email.split("@")[0], email.split("@")[1])
            if not (code == 200 and resp and resp[0]["type"] == "success"):
                dbconn.close()
                raise HTTPException(status_code=502, detail={
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "502",
                    "scimType": "serverError",
                    "detail": "Request failed: upstream API returned an error",
                })
            mailcow_id = resp[0]["msg"][1]
        dbcur.execute("""
            Update users
            SET mailcowId = ?,
                scimId = ?,
                active = ?,
                userName = ?,
                displayName = ?,
                emails = ?
            WHERE id = ?
        """, (
            mailcow_id,
            user.externalId,
            int(user.active),
            user.userName,
            user.displayName,
            json.dumps(user.emails),
            user.id
        ))
        dbcur.execute("""
            UPDATE metrics
            SET value = value + 1
            WHERE name = 'users_updated'
        """)
        dbconn.commit()
        dbconn.close()
        return user
    else:
        raise HTTPException(status_code=400, detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "400",
            "scimType": "invalidSyntax",
            "detail": "Missing required attribute: emails"
        })

# --- Mailcow Helper ---
async def get_mailbox(id: str):
    url = f"{MAILCOW_API_URL}get/mailbox/{id}"
    headers = {"X-API-Key": MAILCOW_API_KEY}
    async with get_async_client() as client:
        resp = await client.post(url, headers=headers)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None

async def create_mailbox(local_part: str, domain: str, name: str):
    url = f"{MAILCOW_API_URL}add/mailbox"
    headers = {"X-API-Key": MAILCOW_API_KEY}
    data = {
        "active": "1",
        "domain": domain,
        "local_part": local_part,
        "name": name,
        "authsource": "generic-oidc",
        "password": "",
        "password2": "",
        "quota": "3072",
        "force_pw_update": "0",
        "tls_enforce_in": "1",
        "tls_enforce_out": "1",
        "tags": ["scim"]
    }
    async with get_async_client() as client:
        resp = await client.post(url, headers=headers, json=data)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None

async def update_mailbox(id: str, active: bool = None, name: str = None, tags: list = None):
    url = f"{MAILCOW_API_URL}edit/mailbox"
    headers = {"X-API-Key": MAILCOW_API_KEY}
    attr = {}
    if active is not None:
        attr["active"] = "1" if active else "0"
    if name is not None:
        attr["name"] = name
    if tags is not None:
        attr["tags"] = tags
    payload = {
        "attr": attr,
        "items": [id]
    }
    async with get_async_client() as client:
        resp = await client.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json()
    
async def rename_mailbox(id: str, local_part: str, domain: str):
    url = f"{MAILCOW_API_URL}edit/rename-mbox"
    headers = {"X-API-Key": MAILCOW_API_KEY}
    attr = {
        "domain": domain,
        "old_local_part": id.split("@")[0],
        "new_local_part": local_part,
        "create_alias": "1"
    }
    payload = {
        "attr": attr,
        "items": [id]
    }
    async with get_async_client() as client:
        resp = await client.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json()

async def delete_mailbox(id: str):
    url = f"{MAILCOW_API_URL}delete/mailbox"
    headers = {"X-API-Key": MAILCOW_API_KEY}
    payload = [id]
    async with get_async_client() as client:
        resp = await client.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json()

# --- SCIM Endpoints ---
@api.get("/healthz")
async def healthz_endpoint():
    return {"status": "running"}

@api.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint():
    return get_metrics()

@api.get("/Users/{user_id}", response_model=SCIMUser)
async def get_user_endpoint(user_id: str, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return get_user(user_id)

@api.get("/Users", response_model=SCIMListResponse)
async def get_users_endpoint(index: int = Query(1), count: int = Query(100), authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return get_users(index, count)

@api.post("/Users", status_code=status.HTTP_201_CREATED, response_model=SCIMUser)
async def post_user_endpoint(user: SCIMUser, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await create_user(user)
    return user

@api.put("/Users/{user_id}", response_model=SCIMUser)
async def put_user_endpoint(user_id: str, user: SCIMUser, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await update_user(user_id, user)
    return user

@api.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_endpoint(user_id: str, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    await delete_user(user_id)
    
@api.get("/Groups/{group_id}", response_model=SCIMGroup)
async def get_group_endpoint(group_id: str, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return SCIMGroup(
        schemas=["urn:ietf:params:scim:schemas:core:2.0:Group"],
        id=group_id,
        displayName=group_id,
        members=[]
    )

@api.get("/Groups", response_model=SCIMListResponse)
async def get_groups_endpoint(index: int = Query(1), count: int = Query(100), authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return SCIMListResponse(
        schemas=["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        totalResults=0,
        itemsPerPage=0,
        startIndex=index,
        Resources=[]
    )
    
@api.post("/Groups", status_code=status.HTTP_201_CREATED, response_model=SCIMGroup)
async def post_group_endpoint(group: SCIMGroup, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return group
    
@api.put("/Groups/{group_id}", response_model=SCIMGroup)
async def patch_group_endpoint(group_id: str, group: SCIMGroup, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return group

@api.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_group_endpoint(group_id: str, authorization: str = Header(None)):
    if authorization != f"Bearer {SCIM_TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    
@api.get("/ServiceProviderConfig")
async def service_provider_config_endpoint():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "id": "mailcow-scim-bridge",
        "documentationUri": "https://github.com/finex7070/mailcow-scim-bridge",
        "patch": {"supported": False},
        "bulk": {"supported": False},
        "filter": {"supported": False},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
    }