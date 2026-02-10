
---

# Birbank Business Synchronization for Odoo 18

This module integrates **Birbank Business (Kapital Bank)** with Odoo 18 Enterprise. It allows you to automatically fetch bank statements (transactions) from your AZN, USD, EUR, and other currency accounts directly into Odoo Accounting.

## 📋 Features

* **Direct API Connection:** Connects via the `my.birbank.business` B2B API.
* **Auto-Link Journals:** Automatically detects existing Odoo Bank Journals by IBAN and links them.
* **Duplicate Prevention:** Uses Birbank's unique `trnRefNo` to ensure transactions are never imported twice.
* **Smart Token Management:** Automatically handles login, token caching (50 mins), and refreshing.
* **Security:** Mimics a real browser to bypass banking firewalls (403 Forbidden errors).
* **Error Notifications:** Sends sticky red alerts to Accounting Managers if a background sync fails.

---

## 🚀 Installation

1. **Prerequisites:**
* Odoo 18 Enterprise.
* Module `account_online_synchronization` must be installed.


2. **Deploy:**
* Place the `kapital_bank_sync` folder into your Odoo `custom_addons` directory.


3. **Activate:**
* Restart the Odoo service.
* Go to **Apps**, click **Update App List**, search for "Birbank", and click **Activate**.



---

## ⚙️ Configuration Guide

### 1. Connect to the Bank

Instead of using the standard "Add Bank Account" wizard, use the dedicated menu:

1. Navigate to **Accounting > Configuration > Add Birbank Account**.
2. **Environment:** Select **Production (Live)** (unless you have Sandbox credentials).
3. **Credentials:** Enter your **Birbank Business Username** and **Password**.
4. **Sync History From:**
* *Important:* Select the date from which you want to start importing transactions.
* *Note:* If you have previously imported statements manually up to Dec 31st, set this date to **Jan 1st**.


5. Click **Connect & Sync**.

### 2. What happens next?

* **Status Check:** The status bar will change to `Connected`.
* **Account Fetching:** Odoo will download all available accounts (IBANs) from Birbank.
* **Auto-Linking:**
* The system looks for an existing **Bank Journal** in Odoo that has the **same IBAN**.
* If found, it links them automatically.
* If **not found**, the account is saved in the background, but you must link it manually (see below).



### 3. Linking Journals Manually (If Auto-Link fails)

If you created a new Bank Account in Birbank but haven't set it up in Odoo yet:

1. Go to **Accounting > Configuration > Journals**.
2. Create a new Journal (Type: Bank).
3. In the **Bank Account** field, you will see a list of account numbers. Select the one corresponding to the Birbank account.
4. Save.

---

## 🔄 Usage

### Automatic Synchronization

Odoo runs a Scheduled Action (Cron) called **"Account: Online Synchronization"** every 12 hours (by default).

* It checks for a valid token.
* If expired, it logs in again automatically.
* It fetches new transactions since the last success date.

### Manual Synchronization

If you need to see a transaction immediately (e.g., for reconciliation):

1. Go to the **Accounting Dashboard**.
2. Find your Birbank Journal card.
3. Click the **"Fetch Transactions"** (or "Synchronize") link.
4. Wait for the success message.

---

## 🛠 Troubleshooting

### Common Errors

| Error Status | Meaning | Solution |
| --- | --- | --- |
| **Status: Draft** | Connection not yet initialized. | Enter username/password and click "Connect & Sync". |
| **Status: Error** | The last attempt failed. | Check the "Last Error" field or the red notification bell. |
| **401 Unauthorized** | Password changed or wrong. | Update password in configuration and click "Connect & Sync". |
| **403 Forbidden** | Bank Firewall blocked the request. | The module automatically mimics a Chrome browser. If this persists, your IP might be blacklisted by the bank. Contact IT. |
| **API Exception** | Internal Bank Error. | Wait 1 hour and try again. This is usually an issue on Birbank's side. |

### "My transactions aren't showing up!"

1. Check the **Sync History From** date in the configuration. Odoo will never fetch transactions older than this date.
2. Check the **Accounting Dashboard**. Are there "Imported" lines waiting to be reconciled?
3. Ensure the **Journal is Linked**. Open the Birbank Configuration page and look at the "Linked Journals" smart button. If it says "0", you need to link the journal in Accounting settings.

---

## 👨‍💻 Technical Notes (For Developers)

* **API Endpoint:** `https://my.birbank.business/api/b2b`
* **Authentication:** JWT Token based. The module caches the token for **50 minutes** to reduce login attempts.
* **User Agent:** The module injects a hardcoded `User-Agent` string (Windows 10 / Chrome) to bypass WAF protections.
* **Transaction ID:** The `trnRefNo` from JSON is mapped to `online_transaction_identifier` to strictly enforce uniqueness constraints at the database level.
* **Date Format:** The module handles `Dec 30, 2025` format parsing with a fallback to `YYYY-MM-DD`.