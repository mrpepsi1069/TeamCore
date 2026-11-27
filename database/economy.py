"""database/economy.py — Cash accounts, the transaction ledger and casino games.

Every member's Cash is one document in `economy_accounts` (one per server). Every change is a
single conditional update:

* a debit only matches while `balance >= amount`, so simultaneous commands can never
  overdraw an account or spend the same Cash twice;
* the ledger row is claimed first and its `_id` is the unique transaction id, so a retried
  or duplicated payment/payout stops before any Cash moves;
* a casino result is claimed with `status: "pending"` in the same way, so a reward is
  credited at most once even if a button is pressed twice or the bot restarts.

Functions take the database handle (`database.handle()` in the bot, `backend.db.db()` on the
website) so both processes can use the same code, like database/teams.py.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from shared.economy import CASINO_GAME_TIMEOUT, STARTING_CASH, check_amount, check_balance, format_cash

ACCOUNTS = "economy_accounts"
LEDGER = "economy_transactions"
GAMES = "casino_games"


class EconomyError(Exception):
    """A problem the member can be told about (not enough Cash, duplicate payment, DB down)."""


class InsufficientCash(EconomyError):
    def __init__(self, balance: int, amount: int):
        self.balance, self.amount = int(balance), int(amount)
        super().__init__(f"You only have {format_cash(self.balance)}, so you can't spend {format_cash(self.amount)}.")


class DuplicateTransaction(EconomyError):
    def __init__(self, txn_id: str):
        self.txn_id = txn_id
        super().__init__("That transaction has already been processed.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _c(dbh, name: str):
    if dbh is None:
        raise EconomyError("The database is unavailable, so no Cash can move right now.")
    return dbh[name]


def _account_id(guild_id, user_id) -> str:
    return f"{guild_id}:{user_id}"


async def ensure_account(dbh, guild_id, user_id) -> dict:
    """This member's Cash account, opened with STARTING_CASH the first time it is needed."""
    accounts = _c(dbh, ACCOUNTS)
    gid, uid = str(guild_id), str(user_id)
    key = {"guild_id": gid, "user_id": uid}
    doc = await accounts.find_one(key)
    if doc:
        return doc
    now = _now()
    try:
        result = await accounts.update_one(
            {"_id": _account_id(gid, uid)},
            {"$setOnInsert": {**key, "balance": STARTING_CASH, "created_at": now, "updated_at": now}},
            upsert=True,
        )
    except DuplicateKeyError:  # another request opened it between our read and write
        result = None
    if result is not None and getattr(result, "upserted_id", None) is not None:
        # Opening the account is the one grant that doesn't come from another balance, so it
        # gets a ledger row too. A duplicate id just means an earlier request already wrote it.
        start_id = f"cash-start:{gid}:{uid}"
        try:
            await _c(dbh, LEDGER).insert_one({
                "_id": start_id, "txn_id": start_id, "kind": "starting", "guild_id": gid,
                "to_id": uid, "amount": STARTING_CASH, "balance_after": STARTING_CASH,
                "status": "done", "created_at": now, "completed_at": now,
            })
        except DuplicateKeyError:
            pass
    doc = await accounts.find_one(key)
    if doc is None:
        raise EconomyError("Could not open a Cash account — please try again.")
    return doc


async def balance(dbh, guild_id, user_id) -> int:
    return int((await ensure_account(dbh, guild_id, user_id))["balance"])


async def balances(dbh, guild_id, user_ids) -> dict:
    """Balances for several members at once (the dashboard's member list needs one query)."""
    ids = [str(u) for u in user_ids]
    if not ids:
        return {}
    rows = await _c(dbh, ACCOUNTS).find(
        {"guild_id": str(guild_id), "user_id": {"$in": ids}}, {"user_id": 1, "balance": 1}
    ).to_list(None)
    return {r["user_id"]: int(r.get("balance") or 0) for r in rows}


async def global_leaderboard(dbh, limit: int = 10) -> list:
    """The richest members across every server the bot is in.

    Cash is stored per server, so a member's global total is the sum of their balances;
    `servers` says in how many of them they actually hold Cash.
    """
    rows = await _c(dbh, ACCOUNTS).aggregate([
        {"$group": {
            "_id": "$user_id",
            "total": {"$sum": "$balance"},
            "servers": {"$sum": {"$cond": [{"$gt": ["$balance", 0]}, 1, 0]}},
        }},
        {"$match": {"total": {"$gt": 0}}},
        {"$sort": {"total": -1, "_id": 1}},
        {"$limit": int(limit)},
    ]).to_list(None)
    return [{"user_id": r["_id"], "total": int(r["total"]), "servers": int(r["servers"])} for r in rows]


async def _claim(dbh, txn_id: str, **fields) -> None:
    """Reserve a transaction id before any Cash moves. Raises DuplicateTransaction if taken."""
    doc = {"_id": txn_id, "txn_id": txn_id, "status": "pending", "created_at": _now(), **fields}
    try:
        await _c(dbh, LEDGER).insert_one(doc)
    except DuplicateKeyError:
        raise DuplicateTransaction(txn_id)


async def _forget(dbh, txn_id: str) -> None:
    """Drop a claim for a transaction that moved nothing, so a retry can still happen."""
    try:
        await _c(dbh, LEDGER).delete_one({"_id": txn_id, "status": "pending"})
    except Exception:
        pass


async def _move(dbh, guild_id, user_id, delta: int, txn_id: str, kind: str, **fields):
    """Move Cash for one account and record it, at most once. Returns the new balance, or
    None when a debit was refused because the account doesn't hold that much."""
    amount = abs(int(delta))
    await _claim(dbh, txn_id, kind=kind, guild_id=str(guild_id), amount=amount, **fields)
    query = {"guild_id": str(guild_id), "user_id": str(user_id)}
    if delta < 0:
        query["balance"] = {"$gte": amount}
    try:
        doc = await _c(dbh, ACCOUNTS).find_one_and_update(
            query,
            {"$inc": {"balance": int(delta)}, "$set": {"updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        await _forget(dbh, txn_id)  # nothing moved; don't block a retry
        raise
    if doc is None:
        await _forget(dbh, txn_id)
        return None
    await _c(dbh, LEDGER).update_one(
        {"_id": txn_id},
        {"$set": {"balance_after": int(doc["balance"]), "status": "done", "completed_at": _now()}},
    )
    return int(doc["balance"])


async def _refund(dbh, guild_id, user_id, amount: int, txn_id: str, kind: str, game_id=None):
    """Give Cash back for a game that never happened / a payment that failed. Never twice."""
    try:
        return await _move(dbh, guild_id, user_id, int(amount), txn_id, kind,
                           to_id=str(user_id), game_id=game_id)
    except DuplicateTransaction:
        return await balance(dbh, guild_id, user_id)


async def transfer(dbh, guild_id, sender_id, recipient_id, amount: int, request_id) -> dict:
    """Send Cash member-to-member. Both sides are written to the ledger, and re-sending the
    same request can't pay twice (the ledger id is derived from the Discord interaction id)."""
    amount = check_amount(amount)
    gid, sender, recipient = str(guild_id), str(sender_id), str(recipient_id)
    if sender == recipient:
        raise EconomyError("You can't send Cash to yourself.")
    await ensure_account(dbh, gid, sender)
    await ensure_account(dbh, gid, recipient)

    base = f"pay:{gid}:{request_id}"
    sender_balance = await _move(dbh, gid, sender, -amount, f"{base}:out", "pay",
                                 from_id=sender, to_id=recipient)
    if sender_balance is None:
        raise InsufficientCash(await balance(dbh, gid, sender), amount)
    try:
        recipient_balance = await _move(dbh, gid, recipient, amount, f"{base}:in", "pay_in",
                                        from_id=sender, to_id=recipient)
    except Exception:
        # The sender was already debited, so put it back before reporting the failure.
        await _refund(dbh, gid, sender, amount, f"{base}:refund", "pay_refund")
        raise
    return {"amount": amount, "sender_balance": sender_balance, "recipient_balance": recipient_balance}


async def open_game(dbh, guild_id, user_id, game: str, entry: int, ttl_seconds: int = CASINO_GAME_TIMEOUT) -> dict:
    """Take the entry and open a pending game. Raises InsufficientCash when the member
    doesn't hold the full entry; a game that can't be stored is refunded immediately."""
    entry = check_amount(entry)
    gid, uid, now = str(guild_id), str(user_id), _now()
    await ensure_account(dbh, gid, uid)
    game_id = uuid4().hex

    new_balance = await _move(dbh, gid, uid, -entry, f"casino:{game_id}:entry", "casino_entry",
                              from_id=uid, game_id=game_id)
    if new_balance is None:
        raise InsufficientCash(await balance(dbh, gid, uid), entry)

    doc = {
        "_id": game_id, "game_id": game_id, "guild_id": gid, "user_id": uid, "game": game,
        "entry": entry, "status": "pending", "reward": 0, "detail": None,
        "created_at": now, "expires_at": now + timedelta(seconds=int(ttl_seconds)),
    }
    try:
        await _c(dbh, GAMES).insert_one(dict(doc))
    except Exception:
        await _refund(dbh, gid, uid, entry, f"casino:{game_id}:void", "casino_refund", game_id=game_id)
        raise
    doc["balance"] = new_balance
    return doc


async def settle_game(dbh, game_id, guild_id, user_id, won: bool, reward: int, detail: str):
    """Settle a pending game exactly once and credit a win. Returns None when it was already
    settled or had expired, so the caller can't pay a result twice."""
    claimed = await _c(dbh, GAMES).find_one_and_update(
        {"game_id": str(game_id), "guild_id": str(guild_id), "status": "pending"},
        {"$set": {"status": "won" if won else "lost", "reward": int(reward or 0),
                  "detail": detail, "settled_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        return None
    if int(reward or 0) > 0:
        try:
            new_balance = await _move(dbh, guild_id, user_id, int(reward), f"casino:{game_id}:payout",
                                      "casino_payout", to_id=str(user_id), game_id=str(game_id))
        except DuplicateTransaction:
            # A retry after the payout was recorded: the reward was already handled.
            new_balance = await balance(dbh, guild_id, user_id)
    else:
        new_balance = await balance(dbh, guild_id, user_id)
    return {"game": claimed, "balance": new_balance}


async def expire_game(dbh, game_id):
    """Expire a pending game that was never played and refund its entry exactly once."""
    claimed = await _c(dbh, GAMES).find_one_and_update(
        {"game_id": str(game_id), "status": "pending", "expires_at": {"$lte": _now()}},
        {"$set": {"status": "expired", "settled_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        return None
    new_balance = await _refund(dbh, claimed["guild_id"], claimed["user_id"], int(claimed["entry"]),
                                f"casino:{game_id}:refund", "casino_refund", game_id=str(game_id))
    return {"game": claimed, "balance": new_balance}


async def _set_balance(dbh, guild_id, user_id, target: int, txn_id: str, **fields) -> int:
    """Write an exact balance (owner tool). The ledger row is claimed first, like every move.
    `target` is validated by admin_adjust, the only caller."""
    await ensure_account(dbh, guild_id, user_id)
    await _claim(dbh, txn_id, kind="admin_set", guild_id=str(guild_id), amount=target,
                 to_id=str(user_id), **fields)
    try:
        doc = await _c(dbh, ACCOUNTS).find_one_and_update(
            {"guild_id": str(guild_id), "user_id": str(user_id)},
            {"$set": {"balance": target, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        await _forget(dbh, txn_id)
        raise
    if doc is None:
        await _forget(dbh, txn_id)
        raise EconomyError("Could not update that Cash account — please try again.")
    await _c(dbh, LEDGER).update_one(
        {"_id": txn_id},
        {"$set": {"balance_after": int(doc["balance"]), "status": "done", "completed_at": _now()}},
    )
    return int(doc["balance"])


async def admin_adjust(dbh, guild_id, user_id, mode: str, amount: int, actor_id, batch_id=None) -> dict:
    """Owner tool: give (`add`), take (`remove`) or `set` one member's Cash.

    Every change goes through the same atomic path as normal play, so a removal can never
    push a balance below $0 and each change gets its own ledger row (linked by `batch_id`
    when it is part of an add-all/remove-all/set-all run).
    """
    mode = str(mode).strip().lower()
    if mode not in ("add", "remove", "set"):
        raise ValueError(f"Unknown Cash mode: {mode}")
    gid, uid = str(guild_id), str(user_id)
    batch = batch_id or uuid4().hex
    txn = f"admin:{batch}:{uid}"
    fields = {"by": str(actor_id), "batch_id": batch}

    if mode == "set":
        # Strict on purpose: a float must be refused, never silently truncated to a whole number.
        target = check_balance(amount)
        new_balance = await _set_balance(dbh, gid, uid, target, txn, **fields)
        return {"amount": target, "balance": new_balance, "batch_id": batch}

    delta = check_amount(amount)
    await ensure_account(dbh, gid, uid)
    if mode == "add":
        new_balance = await _move(dbh, gid, uid, delta, txn, "admin_credit", to_id=uid, **fields)
        if new_balance is None:  # only possible if the account vanished between the two writes
            raise EconomyError("Could not update that Cash account — please try again.")
    else:
        new_balance = await _move(dbh, gid, uid, -delta, txn, "admin_debit", from_id=uid, **fields)
        if new_balance is None:
            raise InsufficientCash(await balance(dbh, gid, uid), delta)
    return {"amount": delta, "balance": new_balance, "batch_id": batch}


async def admin_adjust_many(dbh, guild_id, user_ids, mode: str, amount: int, actor_id) -> dict:
    """Owner tool: the same change for many members at once, one ledger row each.

    A removal skips anyone holding less than the amount instead of overdrawing them, so the
    reply can say exactly how many accounts changed and how many were skipped.
    """
    mode = str(mode).strip().lower()
    if mode not in ("add", "remove", "set"):
        raise ValueError(f"Unknown Cash mode: {mode}")
    amount = check_balance(amount) if mode == "set" else check_amount(amount)
    batch = uuid4().hex
    updated = skipped = 0
    for uid in {str(u) for u in user_ids}:
        try:
            await admin_adjust(dbh, guild_id, uid, mode, amount, actor_id, batch_id=batch)
            updated += 1
        except InsufficientCash:
            skipped += 1
    return {"amount": amount, "updated": updated, "skipped": skipped, "batch_id": batch}


async def expire_due(dbh) -> None:
    """Refund every pending game whose time ran out. Runs at startup and on a timer, so a
    restart (when button views are already gone) can't leave an entry unreconciled."""
    rows = await _c(dbh, GAMES).find(
        {"status": "pending", "expires_at": {"$lte": _now()}}, {"game_id": 1}
    ).to_list(None)
    for row in rows:
        try:
            await expire_game(dbh, row["game_id"])
        except Exception as e:
            print(f"⚠️ casino expiry failed for {row.get('game_id')}: {e}")
