"""Deleting a member's account (required by the App Store; also used by email requests).

DELETE /api/me removes everything linked to the signed-in member: the member record (follows, search words,
watched products, membership level), the record of deals they tapped, and their phones' push registrations.
The app then deletes the sign-in account itself (Firebase Auth) and, for Sign in with Apple, revokes Apple's token.
Shared data that isn't personal stays: deal prices and the last price checks of products (one per product, for everyone).
"""
from flask import Blueprint, jsonify, request
from google.cloud.firestore_v1.base_query import FieldFilter

import members

bp = Blueprint("account", __name__)


def delete_member_data(db, uid):
    """Returns how many records were removed."""
    removed = 0
    ref = db.collection("members").document(uid)
    for c in ref.collection("clicks").stream():
        c.reference.delete()
        removed += 1
    if ref.get().exists:
        ref.delete()
        removed += 1
    for d in db.collection("push_devices").where(filter=FieldFilter("uid", "==", uid)).stream():
        d.reference.delete()
        removed += 1
    return removed


@bp.delete("/api/me")
def delete_me():
    auth = request.headers.get("Authorization", "")
    claims = members.claims_from(auth[7:]) if auth.startswith("Bearer ") else None
    if not claims:
        return jsonify({"error": "Sign in again to delete your account."}), 401
    removed = delete_member_data(members._ctx["db"], claims["user_id"])
    return jsonify({"removed": removed})
