import requests
import logging
import traceback
import json
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


# ==========================================
# HELPER: SAFE STRING ENCODING
# ==========================================
def safe_str(val):
    """Safely return string value, handling None."""
    return str(val) if val else ""


# ==========================================
# 1. JOURNAL: DASHBOARD BUTTON INTERCEPTOR
# ==========================================
class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def manual_sync(self):
        """
        Intercepts the 'Fetch' button on the dashboard.
        """
        _logger.info(f"\n[BIRBANK] >>> DASHBOARD 'manual_sync' CLICKED for Journal ID: {self.id}")
        online_acc = self.account_online_account_id
        if not online_acc:
            return super().manual_sync()

        link = online_acc.account_online_link_id
        if link and link.provider == 'birbank':
            _logger.info(f"[BIRBANK] Targeted Sync for Account: {online_acc.name}")
            # CHANGE: Pass the specific account ID so we don't sync everything
            return link.action_fetch_transactions(target_account_id=online_acc.id)

        return super().manual_sync()


# ==========================================
# 2. LINK MODEL: CONNECTION & API LOGIC
# ==========================================
class AccountOnlineLink(models.Model):
    _inherit = 'account.online.link'

    # --- 1. PROVIDER DEFINITION ---
    provider = fields.Selection(
        selection=[('birbank', 'Birbank Business')],
        string="Provider",
        default='birbank',
        required=True
    )

    # --- 2. CREDENTIALS & CONFIG ---
    birbank_env = fields.Selection([
        ('test', 'Test / Sandbox'),
        ('live', 'Production (Live)')
    ], string="Environment", default='live', required=True)

    birbank_username = fields.Char("Username")
    birbank_password = fields.Char("Password")

    birbank_initial_sync_date = fields.Date(
        string="Sync History From",
        default=lambda self: fields.Date.context_today(self) - timedelta(days=90)
    )

    # --- 3. STATUS FIELDS ---
    state = fields.Selection([
        ('draft', 'Not Connected'),
        ('disconnected', 'Not Connected'),
        ('connected', 'Connected'),
        ('error', 'Action Required')
    ], string="Status", default='draft', readonly=True)

    last_success_date = fields.Datetime("Last Successful Sync", readonly=True)
    last_error_message = fields.Char("Last Error", readonly=True)
    is_date_locked = fields.Boolean(compute='_compute_date_locked')

    # --- 4. TOKEN & COUNTERS ---
    birbank_jwt_token = fields.Char("Token", copy=False)
    birbank_token_expiry = fields.Datetime("Token Expiry", copy=False)
    journal_count = fields.Integer(compute='_compute_journal_count', string="Linked Journals")

    @api.depends('account_online_account_ids')
    def _compute_journal_count(self):
        for link in self:
            count = self.env['account.journal'].search_count([
                ('account_online_account_id.account_online_link_id', '=', link.id)
            ])
            link.journal_count = count

    @api.depends('state')
    def _compute_date_locked(self):
        for record in self:
            record.is_date_locked = (record.state == 'connected')

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_view_journals(self):
        self.ensure_one()
        journals = self.env['account.journal'].search([
            ('account_online_account_id.account_online_link_id', '=', self.id)
        ])
        return {
            'name': _('Linked Journals'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.journal',
            'view_mode': 'list,form',
            'domain': [('id', 'in', journals.ids)],
            'context': {'default_type': 'bank'},
        }

    def action_fetch_transactions(self, target_account_id=None):
        """
        The Main Sync Button Logic.
        Supports fetching ALL accounts (default) or just ONE (if target_account_id is passed).
        """
        self.ensure_one()
        if self.provider != 'birbank':
            return super().action_fetch_transactions()

        _logger.info(f"[BIRBANK] Manual Sync Triggered for Link ID: {self.id}")

        try:
            total_new_lines = 0
            if self.state != 'connected':
                self.write({'state': 'connected'})

            # ---------------------------------------------------------
            # LOGIC CHANGE: Filter accounts if a target is provided
            # ---------------------------------------------------------
            if target_account_id:
                # Sync ONLY the requested account
                accounts_to_process = self.account_online_account_ids.filtered(lambda a: a.id == target_account_id)
            else:
                # Sync ALL accounts (Configuration menu behavior)
                accounts_to_process = self.account_online_account_ids
            # ---------------------------------------------------------

            for online_account in accounts_to_process:
                _logger.info(f"[BIRBANK] Processing Account: {online_account.name}")

                # 1. Fetch raw transactions from API
                result = online_account._retrieve_transactions(date_scraped=None)
                transactions = result.get('transactions', [])

                if not transactions:
                    _logger.info(f"[BIRBANK] No transactions returned for {online_account.name}")
                    continue

                # 2. Attempt to save using our custom manual method
                try:
                    new_lines = online_account._custom_create_lines(transactions)
                    count = len(new_lines)
                    total_new_lines += count
                    _logger.info(f"[BIRBANK] Successfully created {count} new statement lines.")
                except Exception as inner_e:
                    # Log but allow other accounts to proceed if we were syncing multiple
                    _logger.error(f"[BIRBANK] Database Write Failed: {safe_str(inner_e)}")
                    _logger.error(traceback.format_exc())
                    # Only raise immediately if we are targeting a single account (UX is better)
                    if target_account_id:
                        raise UserError(_("Database Write Error on %s: %s") % (online_account.name, safe_str(inner_e)))

            # Update success status (only update Link timestamp if we synced everything or it's the only one)
            if not target_account_id or len(self.account_online_account_ids) == 1:
                self.write({
                    'last_success_date': fields.Datetime.now(),
                    'last_error_message': False,
                    'state': 'connected'
                })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Successful'),
                    'message': _('Process Complete. Added %s new transactions.') % total_new_lines,
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                }
            }

        except Exception as e:
            err_msg = safe_str(e)
            _logger.error(f"[BIRBANK] Manual Sync CRASH: {traceback.format_exc()}")
            self.write({'last_error_message': err_msg, 'state': 'error'})

            if isinstance(e, UserError):
                raise e
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Failed'),
                    'message': f"System Error: {err_msg}",
                    'type': 'danger'
                }
            }

    def action_initialize_connection(self):
        self.ensure_one()
        _logger.info(f"[BIRBANK] >>> Initializing Connection for Link ID {self.id}")
        try:
            self._get_birbank_token(force_refresh=True)
            accounts_list = self._fetch_odoo_fin_accounts()
            _logger.info(f"[BIRBANK] Total Accounts fetched: {len(accounts_list)}")

            existing_accounts = {
                acc.online_identifier: acc
                for acc in self.account_online_account_ids
                if acc.online_identifier
            }

            for idx, acc_data in enumerate(accounts_list):
                identifier = acc_data.get('online_identifier')
                name = acc_data.get('name')
                if not identifier: continue

                _logger.info(f"[BIRBANK] Processing Account [{idx}]: {safe_str(identifier)}")

                vals = {
                    'name': name,
                    'balance': acc_data['balance'],
                    'online_identifier': identifier,
                    'account_number': identifier,
                    'currency_code': acc_data.get('currency_code'),
                    'account_online_link_id': self.id,
                }

                if identifier in existing_accounts:
                    online_acc = existing_accounts[identifier]
                    online_acc.write(vals)
                else:
                    online_acc = self.env['account.online.account'].create(vals)

                # Auto-link logic
                if not online_acc.linked_journal_id:
                    journal = self.env['account.journal'].search([
                        ('type', '=', 'bank'),
                        ('bank_account_id.acc_number', '=', identifier)
                    ], limit=1)
                    if journal:
                        journal.account_online_account_id = online_acc.id
                        if hasattr(online_acc, 'journal_id'):
                            online_acc.journal_id = journal.id

            self.write({
                'state': 'connected',
                'last_success_date': fields.Datetime.now(),
                'last_error_message': False
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _('Accounts fetched and linked.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            err_msg = safe_str(e)
            _logger.error(f"[BIRBANK] Init Failed: {traceback.format_exc()}")
            self.write({'last_error_message': err_msg})
            raise UserError(f"Connection Failed: {err_msg}")

    def action_reset_connection(self):
        self.ensure_one()
        self.state = 'disconnected'

    # -------------------------------------------------------------------------
    # API HELPERS
    # -------------------------------------------------------------------------
    def _get_headers(self, token=None):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive'
        }
        if token:
            headers['Authorization'] = f"Bearer {token}"
        return headers

    def _parse_error(self, e):
        if isinstance(e, requests.exceptions.RequestException) and e.response is not None:
            _logger.error(f"Birbank API Error Status: {e.response.status_code}")
            try:
                body = e.response.text
            except:
                body = "Unprintable Body"
            _logger.error(f"Birbank API Error Body: {body}")
            return f"Bank Error ({e.response.status_code})"
        return safe_str(e)

    def _get_birbank_base_url(self):
        return "https://my.birbank.business/api/b2b" if self.birbank_env == 'live' else "https://pre-my.birbank.business/api/b2b"

    def _get_birbank_token(self, force_refresh=False):
        now = fields.Datetime.now()
        if not force_refresh and self.birbank_jwt_token and self.birbank_token_expiry and self.birbank_token_expiry > (
                now + timedelta(minutes=5)):
            return self.birbank_jwt_token

        url = f"{self._get_birbank_base_url()}/login"
        payload = {"username": self.birbank_username, "password": self.birbank_password}

        try:
            headers = self._get_headers()
            with requests.Session() as s:
                resp = s.post(url, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                token = data.get('responseData', {}).get('jwttoken')
                if not token:
                    raise UserError(f"No token returned.")

                self.sudo().write({
                    'birbank_jwt_token': token,
                    'birbank_token_expiry': now + timedelta(minutes=50)
                })
                return token
        except Exception as e:
            raise UserError(f"Auth Error: {self._parse_error(e)}")

    # -------------------------------------------------------------------------
    # API OVERRIDES (CORE LOGIC)
    # -------------------------------------------------------------------------
    def _fetch_odoo_fin_accounts(self):
        if self.provider != 'birbank':
            return super()._fetch_odoo_fin_accounts()

        token = self._get_birbank_token()
        url = f"{self._get_birbank_base_url()}/accounts"
        headers = self._get_headers(token=token)

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            accounts_data = resp.json().get('responseData', {}).get('accountsList', [])

            return [{
                'online_identifier': acc.get('ibanAcNo'),
                'name': f"{acc.get('acDesc')} ({acc.get('ibanAcNo')}) - {acc.get('ccy')}",
                'balance': float(acc.get('currAmt', 0)),
                'currency_code': acc.get('ccy'),
            } for acc in accounts_data]
        except Exception as e:
            _logger.exception("Failed to fetch Birbank accounts")
            raise UserError(f"Fetch Error: {self._parse_error(e)}")

    def _fetch_odoo_fin_transactions(self, online_account, date_from, date_to):
        """
        The actual API call to Birbank to retrieve transactions.
        """
        if self.provider != 'birbank':
            return super()._fetch_odoo_fin_transactions(online_account, date_from, date_to)

        _logger.info(f"[BIRBANK] Fetching transactions for {safe_str(online_account.name)}")

        try:
            token = self._get_birbank_token()
            url = f"{self._get_birbank_base_url()}/v2/statement/account"

            effective_date_from = date_from or self.birbank_initial_sync_date
            effective_date_to = date_to or fields.Date.today()

            acc_num = online_account.online_identifier
            if not acc_num:
                try:
                    acc_num = online_account.name.split('(')[1].split(')')[0]
                except (IndexError, AttributeError):
                    acc_num = getattr(online_account, 'account_number', False)

            if not acc_num:
                _logger.warning(f"Birbank: Skipping account {online_account.id} - No Account Number found.")
                return []

            params = {
                'accountNumber': acc_num,
                'fromDate': effective_date_from.strftime('%d-%m-%Y'),
                'toDate': effective_date_to.strftime('%d-%m-%Y')
            }

            headers = self._get_headers(token=token)
            resp = requests.get(url, headers=headers, params=params, timeout=45)
            resp.raise_for_status()

            data_block = resp.json().get('responseData', {})
            statement_list = data_block.get('operations', {}).get('statementList', [])

            _logger.info(f"[BIRBANK] Transactions Found: {len(statement_list)}")

            transactions = []
            for st in statement_list:
                raw_date = st.get('trnDt')
                try:
                    txn_date = datetime.strptime(raw_date, '%b %d, %Y').date()
                except (ValueError, TypeError):
                    txn_date = fields.Date.today()

                transactions.append({
                    'online_transaction_identifier': st.get('trnRefNo'),
                    'date': txn_date,
                    'payment_ref': st.get('purpose') or st.get('trnRefNo'),
                    'amount': float(st.get('lcyAmount', 0)),
                    'partner_name': st.get('contrAccount'),
                })

            return transactions

        except Exception as e:
            error_msg = self._parse_error(e)
            _logger.error(f"Birbank Sync Error for {safe_str(online_account.name)}: {error_msg}")
            return []


# ==========================================
# 3. ACCOUNT: THE BRIDGE
# ==========================================
class AccountOnlineAccount(models.Model):
    _inherit = 'account.online.account'

    account_number = fields.Char(string="Account Number")
    linked_journal_id = fields.Many2one('account.journal', compute='_compute_linked_journal', string="Linked Journal")
    is_linked = fields.Boolean(compute='_compute_linked_journal')
    currency_code = fields.Char(string="Currency")

    def _retrieve_transactions(self, date_scraped=None):
        """
        Entry point for transaction fetching.
        """
        self.ensure_one()
        if self.account_online_link_id.provider != 'birbank':
            return super()._retrieve_transactions(date_scraped)

        _logger.info(f"[BIRBANK] Starting retrieval for: {self.name}")

        date_from = date_scraped or self.account_online_link_id.birbank_initial_sync_date
        date_to = fields.Date.today()

        try:
            txns = self.account_online_link_id._fetch_odoo_fin_transactions(self, date_from, date_to)
            _logger.info(f"[BIRBANK] API returned {len(txns)} transactions for {self.name}")
            return {'transactions': txns}
        except Exception as e:
            _logger.error(f"[BIRBANK] Retrieval Failed for {self.name}: {safe_str(e)}")
            return {'transactions': []}

    def _custom_create_lines(self, transactions):
        """
        MANUALLY create bank statement lines.
        Replaces missing Odoo 18 internal methods.
        """
        self.ensure_one()
        StatementLine = self.env['account.bank.statement.line']

        # 1. Resolve Journal
        journal = self.linked_journal_id
        if not journal and hasattr(self, 'journal_id'):
            journal = self.journal_id

        # Double check via search if still not found
        if not journal:
            journal = self.env['account.journal'].search([
                ('account_online_account_id', '=', self.id)
            ], limit=1)

        if not journal:
            raise UserError(f"No Journal linked to account {self.name}. Cannot save transactions.")

        # 2. Duplicate Check
        incoming_ids = [t['online_transaction_identifier'] for t in transactions if
                        t.get('online_transaction_identifier')]

        if incoming_ids:
            # We batch search for existing IDs to avoid looping database calls
            existing_lines = StatementLine.search([
                ('journal_id', '=', journal.id),
                ('online_transaction_identifier', 'in', incoming_ids)
            ])
            existing_ids = set(existing_lines.mapped('online_transaction_identifier'))
        else:
            existing_ids = set()

        to_create = []
        for tx in transactions:
            ref_id = tx.get('online_transaction_identifier')

            # Skip if duplicate
            if ref_id and ref_id in existing_ids:
                continue

            # Prepare creation values
            vals = {
                'date': tx['date'],
                'amount': tx['amount'],
                'payment_ref': tx['payment_ref'],
                'online_transaction_identifier': ref_id,
                'journal_id': journal.id,
            }

            if tx.get('partner_name'):
                vals['partner_name'] = tx['partner_name']

            to_create.append(vals)

        # 3. Create Records
        if to_create:
            return StatementLine.create(to_create)
        return []

    def _compute_linked_journal(self):
        for record in self:
            journal = self.env['account.journal'].search([
                ('account_online_account_id', '=', record.id)
            ], limit=1)

            if not journal and record.account_number:
                journal = self.env['account.journal'].search([
                    ('type', '=', 'bank'),
                    ('bank_account_id.acc_number', '=', record.account_number)
                ], limit=1)

            record.linked_journal_id = journal
            record.is_linked = bool(journal)

            if journal and hasattr(record, 'journal_id') and not record.journal_id:
                try:
                    record.sudo().write({'journal_id': journal.id})
                except Exception:
                    pass

    def action_create_journal(self):
        self.ensure_one()
        if self.linked_journal_id:
            return

        journal_vals = {
            'name': self.name or 'Birbank Journal',
            'type': 'bank',
            'code': (self.currency_code or 'BNK')[:3],
            'account_online_account_id': self.id,
        }

        if self.currency_code:
            curr = self.env['res.currency'].search([('name', '=', self.currency_code)], limit=1)
            if curr:
                journal_vals['currency_id'] = curr.id

        if self.account_number:
            journal_vals['bank_acc_number'] = self.account_number

        new_journal = self.env['account.journal'].create(journal_vals)

        update_vals = {'linked_journal_id': new_journal.id}
        if hasattr(self, 'journal_id'):
            update_vals['journal_id'] = new_journal.id

        self.write(update_vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Journal created and linked successfully!'),
                'type': 'success',
                'sticky': False,
            }
        }