"""Roadmap-selected Streamlit client; domain policy remains in the HTTP API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


class DockItApiError(RuntimeError):
    """Raised when the DockIt API returns a standard error response."""


@dataclass
class DockItApiClient:
    """Small HTTP client used by Streamlit without copied workflow logic."""

    base_url: str
    access_token: str
    timeout_seconds: float = 10.0
    http_client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def close(self) -> None:
        if self.http_client is not None:
            self.http_client.close()

    def list_shipments(self, *, cursor: str | None = None, limit: int = 20) -> dict[str, object]:
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/shipments", params=params)

    def get_options(self, shipment_id: str) -> dict[str, object]:
        return self._request("GET", f"/shipments/{shipment_id}/options")

    def report_delay(self, shipment_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._request("POST", f"/shipments/{shipment_id}/delays", json_body=payload)

    def create_hold(
        self,
        shipment_id: str,
        slot_id: str,
        options_observed_at_utc: str,
    ) -> dict[str, object]:
        return self._request(
            "POST",
            f"/shipments/{shipment_id}/holds",
            json_body={
                "slot_id": slot_id,
                "options_observed_at_utc": options_observed_at_utc,
            },
        )

    def confirm_hold(self, hold_id: str, idempotency_key: str) -> dict[str, object]:
        return self._request(
            "POST",
            f"/holds/{hold_id}/confirm",
            extra_headers={"Idempotency-Key": idempotency_key},
        )

    def cancel_hold(self, hold_id: str) -> dict[str, object]:
        return self._request("DELETE", f"/holds/{hold_id}")

    def send_message(self, thread_id: str, message: str) -> dict[str, object]:
        return self._request(
            "POST",
            f"/chat/{thread_id}/messages",
            json_body={"message": message},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
        if extra_headers is not None:
            headers.update(extra_headers)
        try:
            response = (
                self.http_client.request(
                    method,
                    self.base_url + "/api/v1" + path,
                    headers=headers,
                    params=params,
                    json=json_body,
                )
                if self.http_client is not None
                else httpx.request(
                    method,
                    self.base_url + "/api/v1" + path,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    params=params,
                    json=json_body,
                )
            )
        except httpx.HTTPError as error:
            raise DockItApiError("DockIt API is unavailable") from error
        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = payload.get("message") if isinstance(payload, dict) else None
            raise DockItApiError(str(message or "DockIt API request failed"))
        payload = response.json()
        if not isinstance(payload, dict):
            raise DockItApiError("DockIt API returned an invalid response")
        return {str(key): value for key, value in payload.items()}


def run() -> None:
    """Render the pilot UI as a client of the authenticated HTTP API."""

    import streamlit as st

    st.set_page_config(page_title="DockIt AI", page_icon="D")
    st.title("DockIt AI")
    st.caption("Driver exception management through the DockIt API")

    base_url = st.sidebar.text_input(
        "API base URL",
        value=os.getenv("DOCKIT_API_BASE_URL", "http://127.0.0.1:8000"),
    )
    access_token = st.sidebar.text_input("Cognito access token", type="password")
    if not access_token:
        st.info("Enter a Cognito access token to use the pilot.")
        return

    client = DockItApiClient(base_url, access_token)
    try:
        _render_driver_workspace(st, client)
    except DockItApiError as error:
        st.error(str(error))
    finally:
        client.close()


def _render_driver_workspace(st, client: DockItApiClient) -> None:  # type: ignore[no-untyped-def]
    shipments_payload = client.list_shipments()
    shipments = shipments_payload.get("items", [])
    if not isinstance(shipments, list) or not shipments:
        st.warning("No assigned shipments were returned.")
        return

    shipment_by_id = {
        str(item["shipment_id"]): item
        for item in shipments
        if isinstance(item, dict) and "shipment_id" in item
    }
    shipment_id = st.selectbox("Shipment", list(shipment_by_id))
    st.json(shipment_by_id[shipment_id])

    if st.button("Find feasible options"):
        st.session_state["dockit_options"] = client.get_options(shipment_id)
    options_payload = st.session_state.get("dockit_options")
    if isinstance(options_payload, dict):
        options = options_payload.get("options", [])
        if isinstance(options, list) and options:
            st.subheader("Options")
            st.dataframe(options, use_container_width=True)
            slot_ids = [str(option["slot_id"]) for option in options if isinstance(option, dict)]
            if slot_ids:
                slot_id = st.selectbox("Slot to hold", slot_ids)
                if st.button("Create five-minute hold"):
                    st.session_state["dockit_hold"] = client.create_hold(
                        shipment_id,
                        slot_id,
                        str(options_payload["observed_at_utc"]),
                    )

    hold = st.session_state.get("dockit_hold")
    if isinstance(hold, dict):
        st.info(f"Hold {hold.get('hold_id')} expires at {hold.get('expires_at_utc')}.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm hold"):
            hold_id = str(hold["hold_id"])
            key_name = f"dockit_confirm_key:{hold_id}"
            idempotency_key = st.session_state.setdefault(key_name, f"ui-{uuid4().hex}")
            st.session_state["dockit_booking"] = client.confirm_hold(hold_id, idempotency_key)
        if cancel_col.button("Cancel hold"):
            st.session_state["dockit_booking"] = client.cancel_hold(str(hold["hold_id"]))

    booking = st.session_state.get("dockit_booking")
    if isinstance(booking, dict):
        st.success(f"Server response: {booking}")

    st.subheader("Conversation")
    thread_id = st.text_input(
        "Thread ID",
        value=st.session_state.setdefault("dockit_thread", str(uuid4())),
    )
    message = st.chat_input("Tell DockIt what happened")
    if message:
        response = client.send_message(thread_id, message)
        st.chat_message("user").write(message)
        st.chat_message("assistant").write(response.get("message", "No response"))
