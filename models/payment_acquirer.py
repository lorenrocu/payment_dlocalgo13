# -*- coding: utf-8 -*-
from werkzeug import urls
from odoo import fields, models, _
from odoo.exceptions import ValidationError
from odoo.http import request
import logging
import requests
import json

_logger = logging.getLogger(__name__)

class AcquirerDLocalGo13(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(
        selection_add=[('dlocalgo13', 'DLocal Go 13')]
    )

    # --- URLs (ya existentes y correctas) ---
    dlocalgo13_test_url = fields.Char(
        string='DLocal Go Test API URL',
        default='https://api-sbx.dlocalgo.com' # URL de Sandbox/Pruebas
    )
    dlocalgo13_prod_url = fields.Char(
        string='DLocal Go Production API URL',
        default='https://api.dlocalgo.com' # URL de Producción
    )

    # --- Credenciales de PRODUCCIÓN ---
    dlocalgo13_prod_secret_key = fields.Char(string='Prod Secret Key', password=True, copy=False, groups='base.group_user')
    dlocalgo13_prod_public_key = fields.Char(string='Prod Api Key', copy=False, groups='base.group_user')

    # --- Credenciales de PRUEBA ---
    dlocalgo13_test_secret_key = fields.Char(string='Test Secret Key', password=True, copy=False, groups='base.group_user')
    dlocalgo13_test_public_key = fields.Char(string='Test Api Key', copy=False, groups='base.group_user')

    def _get_dlocalgo13_credentials(self):
        """Devuelve un diccionario con las credenciales correctas según el estado."""
        self.ensure_one()
        if self.state == 'test':
            return {
                'secret_key': self.dlocalgo13_test_secret_key,
                'public_key': self.dlocalgo13_test_public_key,
                'api_url': self.dlocalgo13_test_url,
            }
        else: # 'enabled' (producción) o 'disabled' (usa credenciales de prod por si acaso, o puedes manejarlo diferente)
            return {
                'secret_key': self.dlocalgo13_prod_secret_key,
                'public_key': self.dlocalgo13_prod_public_key,
                'api_url': self.dlocalgo13_prod_url,
            }

    def _dlocalgo13_get_api_url(self):
        """Obtiene la URL base de la API según el estado."""
        self.ensure_one()
        credentials = self._get_dlocalgo13_credentials()
        return f"{credentials['api_url']}/v1/payments"

    def _dlocalgo13_get_headers(self):
        """Obtiene los headers necesarios para la API."""
        self.ensure_one()
        credentials = self._get_dlocalgo13_credentials()
        import base64
        
        # DLocalGo soporta tanto Basic como Bearer auth
        # Probamos primero con Basic auth (más estándar)
        auth_string = f"{credentials['public_key']}:{credentials['secret_key']}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        
        return {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/json'
        }

    def dlocalgo13_get_form_action_url(self):
        self.ensure_one()
        return '/payment/dlocalgo13/initiate'

    def dlocalgo13_form_generate_values(self, values):
        self.ensure_one()
        base_url = self.get_base_url()
        credentials = self._get_dlocalgo13_credentials()

        # Obtener el objeto de moneda
        currency_obj = values.get('currency')
        if isinstance(currency_obj, str):
            currency_obj = self.env['res.currency'].sudo().search([('name', '=', currency_obj)], limit=1)
        elif values.get('currency_id'):
            currency_obj = self.env['res.currency'].browse(values['currency_id'])
        
        partner_first_name = values.get('partner_first_name', '')
        partner_last_name = values.get('partner_last_name', values.get('partner_name', ''))
        customer_name = f"{partner_first_name} {partner_last_name}".strip()
        if not customer_name and values.get('partner_id'):
            partner = self.env['res.partner'].browse(values.get('partner_id'))
            customer_name = partner.name if partner else ''

        # Convertir el monto a float antes de multiplicar por 100
        amount = float(values.get('amount', 0.0))

        dlocalgo13_tx_values = dict(values)
        dlocalgo13_tx_values.update({
            'merchant_id': credentials.get('merchant_id'),
            'public_key': credentials.get('public_key'), # Si se usa en el frontend
            'order_ref': values['reference'],
            'amount_cents': int(round(amount * 100)),
            'currency_code': currency_obj.name if currency_obj else values.get('currency', ''),
            'customer_email': values.get('partner_email'),
            'customer_name': customer_name,
            'return_url': urls.url_join(base_url, '/payment/dlocalgo13/return'),
            'cancel_url': urls.url_join(base_url, values.get('cancel_url') or values.get('return_url') or '/shop/payment'),
            'webhook_url': urls.url_join(base_url, '/payment/dlocalgo13/webhook'),
        })
        _logger.info("DLocalGo13 values para referencia %s (Estado: %s): %s", values['reference'], self.state, dlocalgo13_tx_values)
        return dlocalgo13_tx_values

    def dlocalgo13_call_api_initiate(self, values, tx_sudo):
        """
        Realiza la llamada a la API de DLocal Go para iniciar un pago.
        """
        _logger.info("Enviando a DLocalGo (Estado: %s): URL=%s, Headers=%s, Payload=%s",
                    self.state, self._dlocalgo13_get_api_url(), self._dlocalgo13_get_headers(), values)

        # Preparar los datos para la API
        notification_url = values.get('webhook_url')
        if not notification_url:
            # Si no existe, usar la URL por defecto del webhook
            base_url = self.get_base_url() if hasattr(self, 'get_base_url') else ''
            notification_url = base_url.rstrip('/') + '/payment/dlocalgo13/webhook'
        
        # Obtener información del partner para el payer
        partner_id = values.get('partner_id')
        partner_name = ''
        partner_email = ''
        partner_document = ''
        
        if partner_id:
            partner = self.env['res.partner'].browse(int(partner_id))
            if partner.exists():
                partner_name = partner.name or ''
                partner_email = partner.email or ''
                partner_document = partner.vat or ''
        
        # Usar valores alternativos si no se encontró el partner
        if not partner_name:
            partner_name = values.get('customer_name', values.get('partner_name', 'Cliente'))
        if not partner_email:
            partner_email = values.get('customer_email', values.get('partner_email', ''))
        
        payload = {
            'currency': values['currency'],
            'amount': float(values['amount']),
            'country': 'PE',  # Por defecto Perú
            'order_id': values['reference'],
            'description': f"Pago orden {values['reference']}",
            'success_url': f"{values['return_url']}?order_id={values['reference']}",
            'back_url': values.get('cancel_url') or values.get('return_url') or '/shop/payment',
            'notification_url': notification_url,
            'payer': {
                'name': partner_name,
                'email': partner_email,
                'document': partner_document,
                'user_reference': str(partner_id) if partner_id else ''
            },
            'payment_method_flow': 'REDIRECT'  # Para pagos con redirección
        }
        _logger.info("Payload limpio enviado a DLocalGo: %s", payload)

        try:
            # Log detallado de la petición
            headers_log = {k: v if k != 'Authorization' else 'Basic ***' for k, v in self._dlocalgo13_get_headers().items()}
            _logger.info("=== PETICIÓN A DLOCALGO ===")
            _logger.info("URL: %s", self._dlocalgo13_get_api_url())
            _logger.info("Headers: %s", headers_log)
            _logger.info("Payload: %s", json.dumps(payload, indent=2))
            
            response = requests.post(
                self._dlocalgo13_get_api_url(),
                headers=self._dlocalgo13_get_headers(),
                json=payload
            )
            
            _logger.info("=== RESPUESTA DE DLOCALGO ===")
            _logger.info("Status Code: %s", response.status_code)
            _logger.info("Response Headers: %s", dict(response.headers))
            _logger.info("Response Text: %s", response.text)
            
            response.raise_for_status()
            result = response.json()
            _logger.info("Respuesta JSON de DLocalGo: %s", json.dumps(result, indent=2))

            if result.get('redirect_url'):
                # Actualizar el estado de la transacción
                tx_sudo.write({
                    'state': 'pending',
                    'acquirer_reference': result.get('id'),
                    'state_message': 'Redirigiendo a DLocal Go'
                })
                return result['redirect_url'], result.get('id')
            else:
                _logger.error("No se recibió URL de redirección de DLocalGo")
                tx_sudo.write({
                    'state': 'error',
                    'state_message': 'No se recibió URL de redirección de DLocalGo'
                })
                return False, False

        except requests.exceptions.HTTPError as e:
            error_details = f"HTTP {e.response.status_code}: {e.response.text}" if e.response else str(e)
            _logger.exception("Error HTTP al llamar a DLocalGo: %s", error_details)
            tx_sudo.write({
                'state': 'error',
                'state_message': f'Error HTTP al llamar a DLocalGo: {error_details}'
            })
            return False, False
        except Exception as e:
            _logger.exception("Error al llamar a DLocalGo: %s", str(e))
            tx_sudo.write({
                'state': 'error',
                'state_message': f'Error al llamar a DLocalGo: {str(e)}'
            })
            return False, False