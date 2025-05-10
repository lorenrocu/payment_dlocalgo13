# -*- coding: utf-8 -*-
from odoo import models, fields, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class PaymentTransactionDLocalGo13(models.Model):
    _inherit = 'payment.transaction'

    dlocalgo_payment_method = fields.Char("Método de Pago DLocalGo")
    dlocalgo_payer_name = fields.Char("Nombre del Pagador DLocalGo")
    dlocalgo_payer_email = fields.Char("Email del Pagador DLocalGo")

    def _get_tx_from_feedback_data(self, provider_code, data): # [cite: 41]
        """ Encuentra la transacción basada en el feedback de DLocalGo. """
        tx = super()._get_tx_from_feedback_data(provider_code, data)
        if provider_code != 'dlocalgo13':
            return tx

        # DLocalGo debería devolver tu 'order_ref' o su propio ID de transacción.
        # Ajusta 'dlocalgo_order_id_param' y 'dlocalgo_tx_id_param' a los nombres
        # reales de los parámetros en el webhook/retorno de DLocalGo.
        reference = data.get('dlocalgo_order_id_param') # El que enviaste como 'order_ref'
        acquirer_ref = data.get('dlocalgo_tx_id_param') # El ID de DLocalGo

        if not reference and not acquirer_ref:
            _logger.warning("DLocalGo13: Feedback data no contiene referencia de Odoo ni de la pasarela: %s", data)
            return self.env['payment.transaction'] # Devuelve vacío

        domain = [('acquirer_id.provider', '=', 'dlocalgo13')]
        if acquirer_ref:
            domain.append(('acquirer_reference', '=', acquirer_ref))
        elif reference: # Buscar por referencia de Odoo como fallback si la de la pasarela no está
            domain.append(('reference', '=', reference))
        
        tx_search = self.search(domain, limit=1)
        if not tx_search:
            _logger.warning("DLocalGo13: No se encontró transacción para feedback: %s (Dominio: %s)", acquirer_ref or reference, domain)
            return self.env['payment.transaction'] # Devuelve vacío
        
        _logger.info("DLocalGo13: Transacción encontrada para feedback %s: TX ID %s", acquirer_ref or reference, tx_search.id)
        return tx_search

    def _process_feedback_data(self, provider_code, data):
        """ Procesa el feedback del webhook/retorno de DLocalGo. """
        super_return_val = super()._process_feedback_data(provider_code, data)

        if provider_code != 'dlocalgo13':
            return super_return_val

        # Obtener los datos de la respuesta
        gateway_status = data.get('status')
        gateway_message = data.get('message', '')
        acquirer_ref_from_webhook = data.get('id')  # El ID de la transacción de dLocal Go

        _logger.info("DLocalGo13: Procesando feedback para TX %s (Ref Pasarela: %s). Estado Pasarela: %s. Datos: %s", 
                     self.reference, acquirer_ref_from_webhook, gateway_status, data)

        # Guardar/Actualizar acquirer_reference si dLocal Go lo envía y no lo teníamos
        if acquirer_ref_from_webhook and self.acquirer_reference != acquirer_ref_from_webhook:
            self.acquirer_reference = acquirer_ref_from_webhook
            _logger.info("DLocalGo13: Acquirer reference actualizado a %s para TX %s", acquirer_ref_from_webhook, self.reference)
        
        # Estados de dLocal Go según la documentación
        if gateway_status == 'PAID':
            new_state = 'done'
            self._set_done(state_message=gateway_message)
        elif gateway_status == 'PENDING':
            new_state = 'pending'
            self._set_pending(state_message=gateway_message)
        elif gateway_status == 'CANCELLED':
            new_state = 'cancel'
            self._set_canceled(state_message=gateway_message)
        elif gateway_status in ['REJECTED', 'FAILED']:
            new_state = 'error'
            self._set_error(f"Pago fallido en dLocal Go ({gateway_status}): {gateway_message}")
        else:
            _logger.warning("DLocalGo13: Estado desconocido de pasarela '%s' para TX %s.", gateway_status, self.reference)
            return super_return_val
        
        _logger.info("DLocalGo13: TX %s (Ref Pasarela: %s) movida al estado '%s' basado en estado de pasarela '%s'.", 
                     self.reference, self.acquirer_reference, self.state, gateway_status)
        
        return True