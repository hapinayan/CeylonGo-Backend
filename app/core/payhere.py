from decimal import Decimal
import hashlib
import os


class PayHereService:
    def __init__(self):
        self.merchant_id = os.getenv("PAYHERE_MERCHANT_ID", "TEST_MERCHANT_ID")
        self.merchant_secret = os.getenv("PAYHERE_MERCHANT_SECRET", "TEST_MERCHANT_SECRET")
        self.mode = os.getenv("PAYHERE_MODE", "sandbox").lower()
        self.return_url = os.getenv("PAYHERE_RETURN_URL", "http://localhost:4200/bookings/payment-success")
        self.cancel_url = os.getenv("PAYHERE_CANCEL_URL", "http://localhost:4200/bookings/payment-cancel")
        self.notify_url = os.getenv(
            "PAYHERE_NOTIFY_URL",
            "http://localhost:8000/api/v1/payments/payhere/notify",
        )

    @property
    def checkout_url(self) -> str:
        if self.mode == "production":
            return "https://www.payhere.lk/pay/checkout"
        return "https://sandbox.payhere.lk/pay/checkout"

    def generate_checkout_hash(
        self,
        order_id: str,
        amount: Decimal,
        currency: str = "LKR",
    ) -> str:
        """
        Generate PayHere md5 checkout hash.
        Formula: strtoupper(md5(merchant_id + order_id + amount_formatted + currency + strtoupper(md5(merchant_secret))))
        """
        secret_hash = hashlib.md5(self.merchant_secret.encode("utf-8")).hexdigest().upper()
        amount_formatted = f"{amount:.2f}"
        raw_string = f"{self.merchant_id}{order_id}{amount_formatted}{currency}{secret_hash}"
        return hashlib.md5(raw_string.encode("utf-8")).hexdigest().upper()

    def verify_ipn_signature(
        self,
        order_id: str,
        payhere_amount: str,
        payhere_currency: str,
        status_code: str,
        received_signature: str,
    ) -> bool:
        """
        Verify incoming Instant Payment Notification (IPN) signature.
        Formula: strtoupper(md5(merchant_id + order_id + payhere_amount + payhere_currency + status_code + strtoupper(md5(merchant_secret))))
        """
        secret_hash = hashlib.md5(self.merchant_secret.encode("utf-8")).hexdigest().upper()
        raw_string = (
            f"{self.merchant_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}"
        )
        calculated_sig = hashlib.md5(raw_string.encode("utf-8")).hexdigest().upper()
        return calculated_sig == received_signature.upper()


payhere_service = PayHereService()
