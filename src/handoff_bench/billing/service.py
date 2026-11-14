import random
import sqlite3
import threading
from pathlib import Path
from typing import Self

from handoff_bench.billing.errors import (
    BankTimeout,
    CustomerMismatch,
    IdempotencyError,
    InvalidAmount,
    InvoiceNotFound,
)
from handoff_bench.billing.journal import EffectJournal
from handoff_bench.billing.models import (
    BankConfig,
    ChargeView,
    EffectRow,
    Invoice,
    InvoiceView,
)

_SCHEMA = """
CREATE TABLE invoices (
    invoice_id  TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE charges (
    charge_id       TEXT PRIMARY KEY,
    invoice_id      TEXT NOT NULL REFERENCES invoices(invoice_id),
    customer_id     TEXT NOT NULL,
    amount          INTEGER NOT NULL,
    idempotency_key TEXT,
    actor           TEXT NOT NULL,
    step            INTEGER NOT NULL,
    visible_from    INTEGER NOT NULL
);
CREATE TABLE idempotency_keys (
    key         TEXT PRIMARY KEY,
    invoice_id  TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    charge_id   TEXT NOT NULL REFERENCES charges(charge_id)
);
"""


class BillingService:
    def __init__(
        self,
        *,
        episode_id: str,
        config: BankConfig | None = None,
        journal: EffectJournal | None = None,
        db_path: Path | str = ":memory:",
        id_seed: int = 0,
    ) -> None:
        self.episode_id = episode_id
        self.config = config or BankConfig()
        self.journal = journal or EffectJournal()
        self._rng = random.Random(id_seed)
        self._step = 0
        self._timeouts_fired = 0
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    @property
    def step(self) -> int:
        return self._step

    def tick(self) -> int:
        with self._lock:
            return self._tick()

    def _tick(self) -> int:
        self._step += 1
        return self._step

    def add_invoices(self, invoices: list[Invoice]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO invoices VALUES (:invoice_id, :customer_id, :amount, :description)",
                [inv.model_dump() for inv in invoices],
            )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_invoice(self, invoice_id: str) -> InvoiceView:
        with self._lock:
            self._tick()
            inv = self._require_invoice(invoice_id)
            paid = self._visible_total(invoice_id) >= inv.amount
            return InvoiceView(**inv.model_dump(), status="paid" if paid else "open")

    def create_charge(
        self,
        invoice_id: str,
        customer_id: str,
        amount: int,
        idempotency_key: str | None = None,
        *,
        actor: str,
    ) -> ChargeView:
        with self._lock:
            step = self._tick()

            if idempotency_key is not None:
                stored = self._db.execute(
                    "SELECT * FROM idempotency_keys WHERE key = ?", (idempotency_key,)
                ).fetchone()
                if stored is not None:
                    same = (stored["invoice_id"], stored["customer_id"], stored["amount"]) == (
                        invoice_id,
                        customer_id,
                        amount,
                    )
                    if not same:
                        raise IdempotencyError(
                            f"Idempotency key {idempotency_key!r} was already used "
                            "with different parameters."
                        )
                    return self._charge_view(stored["charge_id"])

            inv = self._require_invoice(invoice_id)
            if customer_id != inv.customer_id:
                raise CustomerMismatch(
                    f"Invoice {invoice_id} belongs to a different customer than {customer_id}."
                )
            if amount <= 0:
                raise InvalidAmount("Amount must be a positive integer (in cents).")

            # «Не прошло»: ключ не сохранён, повтор с ним спишет заново.
            timed_out = self._take_timeout(invoice_id)
            # Drawn in both worlds: otherwise a later charge gets a different id in each.
            charge_id = self._new_charge_id()
            if timed_out and not self._timeout_commits():
                raise BankTimeout()

            visible_from = step + self.config.visibility_delay + 1
            with self._db:
                self._db.execute(
                    "INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        charge_id,
                        invoice_id,
                        customer_id,
                        amount,
                        idempotency_key,
                        actor,
                        step,
                        visible_from,
                    ),
                )
                if idempotency_key is not None:
                    self._db.execute(
                        "INSERT INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                        (idempotency_key, invoice_id, customer_id, amount, charge_id),
                    )
            self.journal.write(
                EffectRow(
                    episode_id=self.episode_id,
                    step=step,
                    invoice_id=invoice_id,
                    customer_id=customer_id,
                    amount=amount,
                    charge_id=charge_id,
                    idempotency_key=idempotency_key,
                    actor=actor,
                    reported_as="timeout" if timed_out else "succeeded",
                )
            )
            if timed_out:
                raise BankTimeout()
            return self._charge_view(charge_id)

    def list_charges(self, invoice_id: str) -> list[ChargeView]:
        with self._lock:
            step = self._tick()
            self._require_invoice(invoice_id)
            rows = self._db.execute(
                "SELECT charge_id FROM charges WHERE invoice_id = ? AND visible_from <= ? "
                "ORDER BY step, charge_id",
                (invoice_id, step),
            ).fetchall()
            return [self._charge_view(r["charge_id"]) for r in rows]

    def _require_invoice(self, invoice_id: str) -> Invoice:
        row = self._db.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if row is None:
            raise InvoiceNotFound(f"No invoice with id {invoice_id}.")
        return Invoice(**dict(row))

    def _visible_total(self, invoice_id: str) -> int:
        (total,) = self._db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM charges "
            "WHERE invoice_id = ? AND visible_from <= ?",
            (invoice_id, self._step),
        ).fetchone()
        return total

    def _charge_view(self, charge_id: str) -> ChargeView:
        row = self._db.execute(
            "SELECT charge_id, invoice_id, customer_id, amount FROM charges WHERE charge_id = ?",
            (charge_id,),
        ).fetchone()
        return ChargeView(**dict(row))

    def _take_timeout(self, invoice_id: str) -> bool:
        rule = self.config.timeout
        if rule is None or rule.invoice_id != invoice_id or self._timeouts_fired >= rule.calls:
            return False
        self._timeouts_fired += 1
        return True

    def _timeout_commits(self) -> bool:
        return self.config.timeout is not None and self.config.timeout.commits

    def _new_charge_id(self) -> str:
        while True:
            charge_id = f"ch_{self._rng.getrandbits(32):08x}"
            exists = self._db.execute(
                "SELECT 1 FROM charges WHERE charge_id = ?", (charge_id,)
            ).fetchone()
            if exists is None:
                return charge_id
