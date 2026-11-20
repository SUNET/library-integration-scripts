#!/usr/bin/python

"""Utilities for exporting MARC records from Libris and importing them into FOLIO."""

import argparse
import copy
import logging
import os
import time
import urllib
from datetime import UTC, datetime, timedelta
from itertools import batched
from pathlib import Path
from typing import Optional

import pymarc
import requests

# Number of MARC records per chunked output file.
CHUNK_SIZE = 200
# Data-import profile schema version constant.
PROFILE_VERSION = 3

# Default FOLIO data-import profile names when not overridden via environment variables.
DEFAULT_FIELD_MAPPINGS_PROFILE_NAME = "Löpande import från Libris"
DEFAULT_CREATE_ACTION_PROFILE_NAME = "Skapa instans löpande Librisimport"
DEFAULT_UPDATE_ACTION_PROFILE_NAME = "Uppdatera instans löpande Librisimport"
DEFAULT_MATCH_PROFILE_NAME = "Löpande Librisimport"
DEFAULT_JOB_PROFILE_NAME = "Löpande Librisimport"

# Default payload used to create a FOLIO field-mapping profile for incoming MARC records.
FIELD_MAPPINGS_PROFILE = {
    "profile": {
        "name": DEFAULT_FIELD_MAPPINGS_PROFILE_NAME,
        "incomingRecordType": "MARC_BIBLIOGRAPHIC",
        "existingRecordType": "INSTANCE",
        "description": "",
        "mappingDetails": {
            "name": "instance",
            "recordType": "INSTANCE",
            "mappingFields": [
                {
                    "name": "discoverySuppress",
                    "enabled": True,
                    "path": "instance.discoverySuppress",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "staffSuppress",
                    "enabled": True,
                    "path": "instance.staffSuppress",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "previouslyHeld",
                    "enabled": True,
                    "path": "instance.previouslyHeld",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "hrid",
                    "enabled": False,
                    "path": "instance.hrid",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "source",
                    "enabled": False,
                    "path": "instance.source",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "catalogedDate",
                    "enabled": True,
                    "path": "instance.catalogedDate",
                    "subfields": [],
                    "value": "###TODAY###",
                },
                {
                    "name": "statusId",
                    "enabled": True,
                    "path": "instance.statusId",
                    "value": "Regular Libris",
                    "subfields": [],
                },
                {
                    "name": "modeOfIssuanceId",
                    "enabled": False,
                    "path": "instance.modeOfIssuanceId",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "statisticalCodeIds",
                    "enabled": True,
                    "path": "instance.statisticalCodeIds[]",
                    "value": "",
                    "subfields": [
                        {
                            "order": 0,
                            "path": "instance.statisticalCodeIds[]",
                            "fields": [
                                {
                                    "name": "statisticalCodeId",
                                    "enabled": True,
                                    "path": "instance.statisticalCodeIds[]",
                                    "value": "RECM (Record management): libris - Data Source Libris",
                                }
                            ],
                        }
                    ],
                    "repeatableFieldAction": "EXTEND_EXISTING",
                },
                {
                    "name": "administrativeNotes",
                    "enabled": True,
                    "path": "instance.administrativeNotes[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "title",
                    "enabled": False,
                    "path": "instance.title",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "alternativeTitles",
                    "enabled": False,
                    "path": "instance.alternativeTitles[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "indexTitle",
                    "enabled": False,
                    "path": "instance.indexTitle",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "series",
                    "enabled": False,
                    "path": "instance.series[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "precedingTitles",
                    "enabled": False,
                    "path": "instance.precedingTitles[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "succeedingTitles",
                    "enabled": False,
                    "path": "instance.succeedingTitles[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "identifiers",
                    "enabled": False,
                    "path": "instance.identifiers[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "contributors",
                    "enabled": False,
                    "path": "instance.contributors[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "publication",
                    "enabled": False,
                    "path": "instance.publication[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "editions",
                    "enabled": False,
                    "path": "instance.editions[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "physicalDescriptions",
                    "enabled": False,
                    "path": "instance.physicalDescriptions[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "instanceTypeId",
                    "enabled": False,
                    "path": "instance.instanceTypeId",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "natureOfContentTermIds",
                    "enabled": True,
                    "path": "instance.natureOfContentTermIds[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "instanceFormatIds",
                    "enabled": False,
                    "path": "instance.instanceFormatIds[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "languages",
                    "enabled": False,
                    "path": "instance.languages[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "publicationFrequency",
                    "enabled": False,
                    "path": "instance.publicationFrequency[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "publicationRange",
                    "enabled": False,
                    "path": "instance.publicationRange[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "notes",
                    "enabled": False,
                    "path": "instance.notes[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "electronicAccess",
                    "enabled": False,
                    "path": "instance.electronicAccess[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "subjects",
                    "enabled": False,
                    "path": "instance.subjects[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "classifications",
                    "enabled": False,
                    "path": "instance.classifications[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "parentInstances",
                    "enabled": True,
                    "path": "instance.parentInstances[]",
                    "value": "",
                    "subfields": [],
                },
                {
                    "name": "childInstances",
                    "enabled": True,
                    "path": "instance.childInstances[]",
                    "value": "",
                    "subfields": [],
                },
            ],
        },
    },
    "addedRelations": [],
    "deletedRelations": [],
}


def login(url: str, tenant: str, username: str, password: str):
    """Request an OpenID Connect password-grant token for a given realm/tenant."""
    print(f"{url}/realms/{tenant}/protocol/openid-connect/token")
    resp = requests.post(
        f"{url}/realms/{tenant}/protocol/openid-connect/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"username={username}&grant_type=password&password={password}",
    )
    return resp


def login2(url: str, tenant: str, username: str, password: str):
    """Authenticate against FOLIO's login-with-expiry endpoint and return the access token cookie."""
    resp = requests.post(
        f"{url}/authn/login-with-expiry",
        json={"username": username, "password": password},
        headers={"x-okapi-tenant": tenant},
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.cookies.get_dict()["folioAccessToken"]


def get_users(url: str, tenant: str, token: str):
    """Fetch FOLIO users using a Bearer access token and tenant header."""
    headers = {"Authorization": f"Bearer {token}", "x-okapi-tenant": tenant}
    resp = requests.get(f"{url}/users", headers=headers)
    resp.raise_for_status()
    return resp


def read_config():
    """Load and parse application configuration from environment variables."""
    isbn_filter = None
    env_isbn_filter = os.environ.get("ISBN_FILTER")
    if env_isbn_filter is not None:
        isbn_filter = env_isbn_filter.split(",")

    return {
        "username": os.environ["FOLIO_USER"],
        "password": os.environ["FOLIO_PASSWORD"],
        "api_url": os.environ["FOLIO_API_URL"],
        "tenant": os.environ["FOLIO_TENANT"],
        "workdir": Path(os.environ["WORKDIR"]),
        "library_name": os.environ["LIBRARY_NAME"],
        "library_longname": os.environ["LIBRARY_LONGNAME"],
        "library_sigels": os.environ["LIBRARY_SIGELS"].split(","),
        "isbn_filter": isbn_filter,
        "interval": int(os.environ["LIBRIS_INTERVAL"]),
        "job_profile_name": os.environ.get("JOB_PROFILE_NAME"),
        "match_profile_name": os.environ.get("MATCH_PROFILE_NAME"),
        "field_mappings_profile_name": os.environ.get("FIELD_MAPPINGS_PROFILE_NAME"),
        "create_action_profile_name": os.environ.get("CREATE_ACTION_PROFILE_NAME"),
        "update_action_profile_name": os.environ.get("UPDATE_ACTION_PROFILE_NAME"),
    }


# Default key-value options sent in the POST body when exporting MARC records from Libris.
LIBRIS_EXPORT_PROPERTIES = {
    "authtype": "none",
    "holdtype": "interleaved",
    # "locations": "O",
    "holddelete": "off",
    "f003": "SE-LIBR",
    "lcsh": "on",
    "isbn": "dehyphenate",
    "move0359": "on",
    "sabtitles": "off",
    "extrafields": "",
    "nameform": "standard",
    "move240to244": "off",
    "move240to500": "off",
    "addauthlinks": "off",
    "generatedewey": "off",
    "generatesab": "off",
    "licensefilter": "on",
    "biblevel": "off",
    "efilter": "off",
    "composestrategy": "compose",
    "format": "ISO2709",
    "characterencoding": "UTF-8",
}


class LibrisClient:
    """Client for exporting ISO2709 MARC records from the Libris API."""

    def __init__(self, target_dir, name, longname, sigels):
        """Initialize export properties and target directory for downloaded MARC files."""
        self.export_properties = dict(
            LIBRIS_EXPORT_PROPERTIES,
            name=name,
            longname=longname,
            locations=" ".join(sigels),
        )
        self.export_properties = "\n".join(
            f"{k}={v}" for k, v in self.export_properties.items()
        ).encode("utf-8")
        logging.info("%s", self.export_properties)
        self.target_dir = Path(target_dir)

    def export(self, start_time, stop_time):
        """Export MARC records between start_time and stop_time and write them to disk."""
        target_file = self.target_dir / f"libris_{start_time}_{stop_time}.mrc"
        start_time_fmt = start_time.isoformat("T").replace("+00:00", "Z")
        stop_time_fmt = stop_time.isoformat("T").replace("+00:00", "Z")
        url = f"https://libris.kb.se/api/marc_export/?from={start_time_fmt}&until={stop_time_fmt}&deleted=ignore&virtualDelete=false"
        # data = "\n".join(f"{k}={v}" for k, v in self.export_properties.items()).encode(
        #     "utf-8"
        # )
        req = urllib.request.Request(url, data=self.export_properties, method="POST")
        n = 0
        with urllib.request.urlopen(req) as response:
            buf = response.read(2048)
            with open(target_file, "wb") as f:
                while buf:
                    n += len(buf)
                    f.write(buf)
                    buf = response.read(2048)
        logging.info("received %i bytes", n)


def chunk_files(input_dir: Path, chunks_dir: Path, isbn_filter=None):
    """Filter, normalize, split MARC files into chunks, and remove processed source files."""
    for path in input_dir.iterdir():
        # if path.stat().st_size == 0:
        #     continue
        logging.info("chunking %s", path)
        with path.open("rb") as data:
            reader = pymarc.MARCReader(data)
            records = [record for record in reader if record is not None]
            num_records = len(records)
            if isbn_filter is not None:
                records = [record for record in records if record.isbn in isbn_filter]
                if num_records > 0:
                    logging.info(
                        "%i out of %i records matched the isbn filter",
                        len(records),
                        num_records,
                    )
            else:
                if num_records > 0:
                    logging.info("%i records found", num_records)
            batches = batched(records, CHUNK_SIZE)
            for idx, batch in enumerate(batches):
                with (chunks_dir / f"{path.stem}_{idx}.mrc").open("wb") as out:
                    for record in batch:
                        # Strip 035 fields and subfield $9 from 830 series fields before import.
                        record.remove_fields("035")
                        if record.get("830"):
                            while record["830"].get_subfields("9"):
                                record["830"].delete_subfield("9")
                        out.write(record.as_marc())
        path.unlink()


class FolioClient:
    """Client for authenticating with FOLIO, managing import profiles, and running data-import jobs."""

    def __init__(
        self,
        url: str,
        tenant: str,
        username: str,
        password: str,
        job_profile_name: Optional[str] = None,
        match_profile_name: Optional[str] = None,
        field_mappings_profile_name: Optional[str] = None,
        create_action_profile_name: Optional[str] = None,
        update_action_profile_name: Optional[str] = None,
    ):
        """Authenticate with FOLIO and configure profile names for data import."""
        self.url = url
        access_token = self.login(url, tenant, username, password)
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "x-okapi-tenant": tenant,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.job_profile_name = job_profile_name or DEFAULT_JOB_PROFILE_NAME
        self.match_profile_name = match_profile_name or DEFAULT_MATCH_PROFILE_NAME
        self.field_mappings_profile_name = (
            field_mappings_profile_name or DEFAULT_FIELD_MAPPINGS_PROFILE_NAME
        )
        self.create_action_profile_name = (
            create_action_profile_name or DEFAULT_CREATE_ACTION_PROFILE_NAME
        )
        self.update_action_profile_name = (
            update_action_profile_name or DEFAULT_UPDATE_ACTION_PROFILE_NAME
        )

    def login(self, url: str, tenant: str, username: str, password: str):
        """Log in to FOLIO and return the access token cookie."""
        resp = requests.post(
            f"{url}/authn/login-with-expiry",
            json={"username": username, "password": password},
            headers={"x-okapi-tenant": tenant},
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.cookies.get_dict()["folioAccessToken"]

    def curl(self, path):
        """Perform an authenticated GET request against a relative FOLIO API path."""
        response = requests.get(
            f"{self.url}/{path}",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def delete(self, path, id):
        """Perform an authenticated DELETE request for a resource by ID and return the status code."""
        new_headers = self.headers.copy()
        new_headers.pop("Accept")
        new_headers.pop("Content-Type")
        response = requests.delete(
            f"{self.url}/{path}/{id}",
            headers=new_headers,
        )
        response.raise_for_status()
        return response.status_code

    def upload_definitions(self, filename):
        """Create a data-import upload definition for the given filename."""
        logging.info("creating upload definition for %s", filename)
        response = requests.post(
            f"{self.url}/data-import/uploadDefinitions",
            json={"fileDefinitions": [{"name": filename}]},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def upload_file(
        self, upload_definition_id: int, file_definition_id: int, data: bytes
    ):
        """Upload raw MARC bytes to an existing FOLIO data-import file definition."""
        logging.info("uploading file to %s", upload_definition_id)
        headers = self.headers.copy()
        headers["Content-Type"] = "application/octet-stream"
        response = requests.post(
            f"{self.url}/data-import/uploadDefinitions/{upload_definition_id}/files/{file_definition_id}",
            headers=headers,
            data=data,
        )
        response.raise_for_status()
        return response.json()

    def process_file(self, upload_file):
        """Start a MARC data-import job for an uploaded file using the configured job profile."""
        upload_definition_id = upload_file["fileDefinitions"][0]["uploadDefinitionId"]
        filename = upload_file["fileDefinitions"][0]["sourcePath"]
        logging.info("processing file %s", filename)
        job_profile = self.ensure_job_profile()
        data = {
            "uploadDefinition": upload_file,
            "jobProfileInfo": {
                # TODO
                "id": job_profile["id"],
                "name": self.job_profile_name,
                "dataType": "MARC",
            },
        }
        response = requests.post(
            f"{self.url}/data-import/uploadDefinitions/{upload_definition_id}/processFiles?defaultMapping=true",
            headers=self.headers,
            json=data,
        )
        response.raise_for_status()
        return response

    def progress(self, filename):
        """Retrieve the most recent job execution status record for a given filename."""
        response = requests.get(
            f"{self.url}/metadata-provider/jobExecutions",
            headers=self.headers,
            params={
                "filename": filename,
                "limit": 1000,
            },
        )
        response.raise_for_status()
        return response.json()["jobExecutions"][-1]

    def wait(self, filename):
        """Poll until the import job for filename completes or fails, raising on timeout or error."""
        for num in range(100):
            progress = self.progress(filename)
            if progress.get("completedDate"):
                break
            time.sleep(2)
            continue
        else:
            raise Exception(f"timeout waiting for {str(progress)}")
        if progress["status"] != "COMMITTED":
            raise Exception(f"job failed: {str(progress)}")
        return progress

    def get_field_mapping_profiles(self):
        """Fetch existing field-mapping profiles from FOLIO."""
        resp = requests.get(
            f"{self.url}/data-import-profiles/mappingProfiles",
            headers=self.headers,
            params={"limit": 500},
        )
        resp.raise_for_status()
        return resp.json()["mappingProfiles"]

    def ensure_field_mapping_profile(self):
        """Return the configured field-mapping profile, creating it if it does not exist."""
        field_mapping_profiles = self.get_field_mapping_profiles()
        if self.field_mappings_profile_name not in (
            profile["name"] for profile in field_mapping_profiles
        ):
            logging.info("creating field mapping profile")
            field_mappings_profile = copy.deepcopy(FIELD_MAPPINGS_PROFILE)
            field_mappings_profile["profile"]["name"] = self.field_mappings_profile_name
            resp = requests.post(
                f"{self.url}/data-import-profiles/mappingProfiles",
                json=field_mappings_profile,
                headers=self.headers,
            )
            resp.raise_for_status()
            field_mapping_profiles = self.get_field_mapping_profiles()
        return next(
            profile
            for profile in field_mapping_profiles
            if profile["name"] == self.field_mappings_profile_name
        )

    def get_action_profiles(self):
        """Fetch existing action profiles from FOLIO."""
        resp = requests.get(
            f"{self.url}/data-import-profiles/actionProfiles",
            headers=self.headers,
            params={"limit": 500},
        )
        resp.raise_for_status()
        return resp.json()["actionProfiles"]

    def ensure_create_action_profile(self):
        """Return the CREATE action profile for Instances, creating it if it does not exist."""
        action_profiles = self.get_action_profiles()
        if self.create_action_profile_name not in (
            profile["name"] for profile in action_profiles
        ):
            logging.info("creating action profile")
            field_mapping_profile = self.ensure_field_mapping_profile()
            resp = requests.post(
                f"{self.url}/data-import-profiles/actionProfiles",
                json={
                    "profile": {
                        "name": self.create_action_profile_name,
                        "description": "",
                        "action": "CREATE",
                        "folioRecord": "INSTANCE",
                    },
                    "addedRelations": [
                        {
                            "masterProfileId": None,
                            "masterProfileType": "ACTION_PROFILE",
                            "detailProfileId": field_mapping_profile["id"],
                            "detailProfileType": "MAPPING_PROFILE",
                        }
                    ],
                    "deletedRelations": [],
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            action_profiles = self.get_action_profiles()
        return next(
            profile
            for profile in action_profiles
            if profile["name"] == self.create_action_profile_name
        )

    def ensure_update_action_profile(self):
        """Return the UPDATE action profile for Instances, creating it if it does not exist."""
        action_profiles = self.get_action_profiles()
        if self.update_action_profile_name not in (
            profile["name"] for profile in action_profiles
        ):
            logging.info("creating action profile")
            field_mapping_profile = self.ensure_field_mapping_profile()
            resp = requests.post(
                f"{self.url}/data-import-profiles/actionProfiles",
                json={
                    "profile": {
                        "name": self.update_action_profile_name,
                        "description": "",
                        "action": "UPDATE",
                        "folioRecord": "INSTANCE",
                    },
                    "addedRelations": [
                        {
                            "masterProfileId": None,
                            "masterProfileType": "ACTION_PROFILE",
                            "detailProfileId": field_mapping_profile["id"],
                            "detailProfileType": "MAPPING_PROFILE",
                        }
                    ],
                    "deletedRelations": [],
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            action_profiles = self.get_action_profiles()
        return next(
            profile
            for profile in action_profiles
            if profile["name"] == self.update_action_profile_name
        )

    def get_match_profiles(self):
        """Fetch existing match profiles from FOLIO."""
        resp = requests.get(
            f"{self.url}/data-import-profiles/matchProfiles",
            headers=self.headers,
            params={"limit": 500},
        )
        resp.raise_for_status()
        return resp.json()["matchProfiles"]

    def ensure_match_profile(self):
        """Return the Libris identifier match profile, creating it if it does not exist."""
        match_profiles = self.get_match_profiles()
        if self.match_profile_name not in (
            profile["name"] for profile in match_profiles
        ):
            logging.info("creating match profile")
            identifier_types = self.get_identifier_types()
            libris_id_uuid = next(
                # Is currently "System control number", "Libris ID" is unused
                t["id"]
                for t in identifier_types
                if t["name"] == "System control number"
            )
            resp = requests.post(
                f"{self.url}/data-import-profiles/matchProfiles",
                json={
                    "profile": {
                        "name": self.match_profile_name,
                        "description": "",
                        "incomingRecordType": "MARC_BIBLIOGRAPHIC",
                        "matchDetails": [
                            {
                                "incomingRecordType": "MARC_BIBLIOGRAPHIC",
                                "incomingMatchExpression": {
                                    "fields": [
                                        {"label": "field", "value": "001"},
                                        {"label": "indicator1", "value": ""},
                                        {"label": "indicator2", "value": ""},
                                        {"label": "recordSubfield", "value": ""},
                                    ],
                                    "staticValueDetails": None,
                                    "dataValueType": "VALUE_FROM_RECORD",
                                    "qualifier": {
                                        "qualifierType": "BEGINS_WITH",
                                        "qualifierValue": "(LIBRIS)",
                                    },
                                },
                                "existingRecordType": "INSTANCE",
                                "existingMatchExpression": {
                                    "fields": [
                                        {
                                            "label": "field",
                                            "value": "instance.identifiers[].value",
                                        },
                                        {
                                            "label": "identifierTypeId",
                                            "value": libris_id_uuid,
                                        },
                                    ],
                                    "qualifier": {
                                        "qualifierType": "BEGINS_WITH",
                                        "qualifierValue": "(Libris)",
                                    },
                                    "dataValueType": "VALUE_FROM_RECORD",
                                },
                                "matchCriterion": "EXACTLY_MATCHES",
                            }
                        ],
                        "existingRecordType": "INSTANCE",
                    },
                    "addedRelations": [],
                    "deletedRelations": [],
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            match_profiles = self.get_match_profiles()
        return next(
            profile
            for profile in match_profiles
            if profile["name"] == self.match_profile_name
        )

    def get_job_profiles(self):
        """Fetch existing job profiles from FOLIO."""
        resp = requests.get(
            f"{self.url}/data-import-profiles/jobProfiles",
            headers=self.headers,
            params={"limit": 500},
        )
        resp.raise_for_status()
        return resp.json()["jobProfiles"]

    def ensure_job_profile(self):
        """Return the MARC import job profile, creating it and its dependencies if missing."""
        job_profiles = self.get_job_profiles()
        if self.job_profile_name not in (profile["name"] for profile in job_profiles):
            logging.info("creating job profile")
            match_profile = self.ensure_match_profile()
            create_action_profile = self.ensure_create_action_profile()
            update_action_profile = self.ensure_update_action_profile()
            resp = requests.post(
                f"{self.url}/data-import-profiles/jobProfiles",
                json={
                    "profile": {
                        "name": self.job_profile_name,
                        "description": "",
                        "dataType": "MARC",
                    },
                    "addedRelations": [
                        {
                            "masterProfileId": None,
                            "masterWrapperId": None,
                            "masterProfileType": "JOB_PROFILE",
                            "detailProfileId": match_profile["id"],
                            "detailWrapperId": None,
                            "detailProfileType": "MATCH_PROFILE",
                            "order": 0,
                        },
                        {
                            "masterProfileId": match_profile["id"],
                            "masterWrapperId": None,
                            "masterProfileType": "MATCH_PROFILE",
                            "detailProfileId": update_action_profile["id"],
                            "detailWrapperId": None,
                            "detailProfileType": "ACTION_PROFILE",
                            "order": 0,
                            "reactTo": "MATCH",
                        },
                        {
                            "masterProfileId": match_profile["id"],
                            "masterWrapperId": None,
                            "masterProfileType": "MATCH_PROFILE",
                            "detailProfileId": create_action_profile["id"],
                            "detailWrapperId": None,
                            "detailProfileType": "ACTION_PROFILE",
                            "order": 0,
                            "reactTo": "NON_MATCH",
                        },
                    ],
                    "deletedRelations": [],
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            job_profiles = self.get_job_profiles()
        return next(
            profile
            for profile in job_profiles
            if profile["name"] == self.job_profile_name
        )

    def get_identifier_types(self):
        resp = requests.get(
            f"{self.url}/identifier-types",
            headers=self.headers,
            params={"limit": 500},
        )
        resp.raise_for_status()
        return resp.json()["identifierTypes"]


def upload_chunk(client: FolioClient, path: Path):
    data = path.read_bytes()

    upload_definition = client.upload_definitions(path.name)
    definition_id = upload_definition["id"]
    file_definition_id = upload_definition["fileDefinitions"][0]["id"]

    upload_file = client.upload_file(definition_id, file_definition_id, data)
    process_file = client.process_file(upload_file)
    return process_file


def cleanup(client: FolioClient):
    definitions = client.curl("data-import/uploadDefinitions")["uploadDefinitions"]
    to_delete = []
    for definition in definitions:
        for file_def in definition["fileDefinitions"]:
            if file_def["name"].startswith("import0"):
                to_delete.append(definition)
    for d in to_delete:
        logging.info("deleting definition %s", d["id"])
        client.delete("data-import/uploadDefinitions", d["id"])


def mainloop():
    config = read_config()
    workdir = config["workdir"]
    libris_client = LibrisClient(
        workdir / "input",
        config["library_name"],
        config["library_longname"],
        config["library_sigels"],
    )
    logging.info("starting")
    timestamp_path = workdir / "timestamp.txt"
    interval = timedelta(seconds=config["interval"])
    isbn_filter = config["isbn_filter"]
    input_path = workdir / "input"
    chunks_path = workdir / "chunks"

    input_path.mkdir(parents=True, exist_ok=True)
    chunks_path.mkdir(parents=True, exist_ok=True)
    folio_client = FolioClient(
        config["api_url"],
        config["tenant"],
        config["username"],
        config["password"],
        job_profile_name=config["job_profile_name"],
        match_profile_name=config["match_profile_name"],
        field_mappings_profile_name=config["field_mappings_profile_name"],
        create_action_profile_name=config["create_action_profile_name"],
        update_action_profile_name=config["update_action_profile_name"],
    )
    folio_client.ensure_job_profile()
    if not timestamp_path.exists():
        with open(timestamp_path, "w") as f_:
            # t = datetime(2026, 9, 1, 15, 20, 42, 6, UTC)
            t = datetime.now(UTC)
            f_.write(t.isoformat("T").replace("+00:00", "Z"))
    with open(timestamp_path, "r") as f_:
        last_iteration = datetime.fromisoformat(f_.read())
    while True:
        # chunk files
        chunk_files(input_path, chunks_path, isbn_filter=isbn_filter)

        # import chunks
        chunk_paths = list(chunks_path.iterdir())
        if chunk_paths:
            folio_client = FolioClient(
                config["api_url"],
                config["tenant"],
                config["username"],
                config["password"],
                job_profile_name=config["job_profile_name"],
                match_profile_name=config["match_profile_name"],
                field_mappings_profile_name=config["field_mappings_profile_name"],
                create_action_profile_name=config["create_action_profile_name"],
                update_action_profile_name=config["update_action_profile_name"],
            )
            for path in chunk_paths:
                logging.info("uploading %s to folio", path)
                upload_chunk(folio_client, path)
                path.unlink()

        # fetch updates from libris
        next_iteration = last_iteration + interval
        now = datetime.now(UTC)
        if next_iteration > now:
            time.sleep((next_iteration - now).seconds)
        logging.info(
            "retrieving updates from libris %s - %s",
            last_iteration.isoformat(),
            next_iteration.isoformat(),
        )
        libris_client.export(last_iteration, next_iteration)
        last_iteration = next_iteration
        with open(timestamp_path, "w") as f_:
            f_.write(last_iteration.isoformat())


def fetch(start, end):
    config = read_config()
    workdir = config["workdir"]
    libris_client = LibrisClient(
        workdir / "input",
        config["library_name"],
        config["library_longname"],
        config["library_sigels"],
    )
    libris_client.export(start, end)


def chunk():
    config = read_config()
    workdir = config["workdir"]
    input_path = workdir / "input"
    chunks_path = workdir / "chunks"
    chunk_files(input_path, chunks_path)


def upload(path):
    config = read_config()
    folio_client = FolioClient(
        config["api_url"],
        config["tenant"],
        config["username"],
        config["password"],
        job_profile_name=config["job_profile_name"],
        match_profile_name=config["match_profile_name"],
        field_mappings_profile_name=config["field_mappings_profile_name"],
        create_action_profile_name=config["create_action_profile_name"],
        update_action_profile_name=config["update_action_profile_name"],
    )
    folio_client.ensure_job_profile()
    upload_chunk(folio_client, path)


def parseargs():
    p = argparse.ArgumentParser(description="löpande import från Libris XL")
    p.add_argument(
        "action",
        help="action to perform",
        choices=["run", "fetch", "chunk", "upload"],
    )
    p.add_argument("--filename", "-f", type=Path, help="file to operate on")
    p.add_argument("--start", help="start time")
    p.add_argument("--end", help="end time")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = parseargs()
    if args.action == "run":
        mainloop()
    elif args.action == "fetch":
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        fetch(start, end)
    elif args.action == "chunk":
        chunk()
    elif args.action == "upload":
        upload(Path(args.filename))
