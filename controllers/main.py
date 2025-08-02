# -*- coding: utf-8 -*-
import logging
import pprint
import werkzeug # Para redirecciones y respuestas
from odoo import http
from odoo.http import request
import time
import json
import requests

_logger = logging.getLogger(__name__)

class DLocalGo13Controller(http.Controller):
    _initiate_url = '/payment/dlocalgo13/initiate' # [cite: 73]
    _return_url = '/payment/dlocalgo13/return'   # [cite: 117]
    _cancel_url = '/payment/dlocalgo13/cancel'
    _webhook_url = '/payment/dlocalgo13/webhook' # [cite: 95]

    @http.route(_initiate_url, type='http', auth='public', methods=['POST'], csrf=True, website=True)
    def dlocalgo13_initiate_payment(self, **post):
        """
        Esta ruta se llama cuando el usuario hace clic en "Pagar ahora" con DLocalGo13.
        Prepara la transacción en Odoo y redirige al usuario a DLocalGo o
        realiza una llamada S2S para obtener una URL de pago.
        """
        try:
            _logger.info("=== INICIO DLocalGo13 Initiate ===")
            _logger.info("Headers recibidos: %s", dict(request.httprequest.headers))
            _logger.info("Datos POST recibidos: %s", pprint.pformat(post))
            _logger.info("URL completa: %s", request.httprequest.url)
            
            # Obtener el acquirer por provider en lugar de ID
            acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'dlocalgo13')], limit=1)
            if not acquirer:
                _logger.warning("DLocalGo13: No se encontró el acquirer de DLocalGo13")
                return request.redirect('/shop/payment?error=invalid_acquirer')

            # Obtener el partner_id de la sesión si no está en los datos POST
            if not post.get('partner_id'):
                if request.env.user._is_public():
                    reference = post.get('reference', '')
                    # Extraer la referencia base (sin el sufijo -1, -2, etc.)
                    base_reference = reference.split('-')[0] if '-' in reference else reference
                    _logger.info("DLocalGo13: Buscando orden con referencia base: %s", base_reference)
                    order = request.env['sale.order'].sudo().search([
                        '|',
                        ('name', '=', base_reference),
                        ('client_order_ref', '=', base_reference)
                    ], limit=1)
                    if order:
                        _logger.info("DLocalGo13: Orden encontrada: %s", order.name)
                        if order.partner_id:
                            post['partner_id'] = order.partner_id.id
                            _logger.info("DLocalGo13: Usando partner_id de la orden: %s", post['partner_id'])
                        else:
                            _logger.error("DLocalGo13: La orden %s no tiene partner asociado", order.name)
                            return request.redirect('/shop/payment?error=order_no_partner')
                    else:
                        _logger.warning("DLocalGo13: No se encontró orden con referencia %s, intentando con order_id", base_reference)
                        if post.get('order_id'):
                            try:
                                order_id_int = int(post.get('order_id'))
                                order = request.env['sale.order'].sudo().browse(order_id_int)
                                if order.exists():
                                    _logger.info("DLocalGo13: Orden encontrada por order_id: %s", order.name)
                                    if order.partner_id:
                                        post['partner_id'] = order.partner_id.id
                                        _logger.info("DLocalGo13: Usando partner_id de la orden: %s", post['partner_id'])
                                    else:
                                        _logger.error("DLocalGo13: La orden %s no tiene partner asociado", order.name)
                                        return request.redirect('/shop/payment?error=order_no_partner')
                                else:
                                    _logger.error("DLocalGo13: No se encontró ninguna orden con order_id=%s", order_id_int)
                                    return request.redirect('/shop/payment?error=order_not_found')
                            except ValueError:
                                _logger.error("DLocalGo13: order_id inválido %s", post.get('order_id'))
                                return request.redirect('/shop/payment?error=invalid_order_id')
                        else:
                            _logger.error("DLocalGo13: No se pudo encontrar la orden con referencia %s y no se proporcionó order_id", base_reference)
                            return request.redirect('/shop/payment?error=order_not_found')
                else:
                    post['partner_id'] = request.env.user.partner_id.id
                    _logger.info("DLocalGo13: Usando partner_id de la sesión: %s", post['partner_id'])

            # Asegurarnos de que tenemos los datos necesarios
            required_fields = ['amount', 'currency', 'reference']
            missing_fields = [field for field in required_fields if not post.get(field)]
            if missing_fields:
                _logger.error("DLocalGo13: Faltan campos requeridos: %s", missing_fields)
                return request.redirect('/shop/payment?error=missing_required_fields')

            # Preparar los datos para el acquirer
            post['currency_id'] = request.env['res.currency'].sudo().search([('name', '=', post['currency'])], limit=1).id
            if not post['currency_id']:
                _logger.error("DLocalGo13: Moneda no encontrada: %s", post['currency'])
                return request.redirect('/shop/payment?error=invalid_currency')

            # Extraer la referencia base (sin el sufijo -1, -2, etc.)
            base_reference = post['reference'].split('-')[0] if '-' in post['reference'] else post['reference']
            post['reference'] = base_reference  # Usar siempre la referencia base

            # Buscar transacción existente con la referencia base
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('reference', '=', base_reference),
                ('acquirer_id', '=', acquirer.id)
            ], order='id desc', limit=1)

            if not tx_sudo:
                # Si no existe ninguna transacción, crear la primera con la referencia base
                try:
                    tx_sudo = request.env['payment.transaction'].sudo().create({
                        'acquirer_id': acquirer.id,
                        'reference': base_reference,
                        'amount': float(post.get('amount')),
                        'currency_id': post['currency_id'],
                        'partner_id': int(post.get('partner_id')),
                        'state': 'draft',
                        'return_url': post.get('return_url'),
                        'sale_order_ids': [(6, 0, [int(post.get('order_id'))])] if post.get('order_id') else None,
                    })
                    _logger.info("DLocalGo13: Creada primera transacción con referencia: %s", base_reference)
                except Exception as e:
                    _logger.error("DLocalGo13: Error al crear primera transacción: %s", str(e))
                    # Si falla la creación, buscar la última transacción
                    tx_sudo = request.env['payment.transaction'].sudo().search([
                        ('reference', '=', base_reference),
                        ('acquirer_id', '=', acquirer.id)
                    ], order='id desc', limit=1)
                    if not tx_sudo:
                        return request.redirect('/shop/payment?error=transaction_creation_failed')

            _logger.info("DLocalGo13: Transacción %s (ID: %s) creada/encontrada en estado %s.", 
                        tx_sudo.reference, tx_sudo.id, tx_sudo.state)

            # Llamada Servidor-a-Servidor (S2S) a la API de DLocalGo
            payment_url_from_dlocalgo, gateway_tx_ref_from_dlocalgo = \
                acquirer.sudo().dlocalgo13_call_api_initiate(post, tx_sudo)

            if payment_url_from_dlocalgo:
                _logger.info("DLocalGo13: URL de pago de DLocalGo recibida: %s", payment_url_from_dlocalgo)
                if gateway_tx_ref_from_dlocalgo:
                    tx_sudo.acquirer_reference = gateway_tx_ref_from_dlocalgo
                    _logger.info("DLocalGo13: Ref de pasarela %s guardada para TX %s", 
                               gateway_tx_ref_from_dlocalgo, tx_sudo.reference)
                return werkzeug.utils.redirect(payment_url_from_dlocalgo, code=303)
            else:
                error_msg = tx_sudo.state_message or "DLocalGo initiation failed (no payment URL)"
                _logger.error("DLocalGo13: Fallo al iniciar con DLocalGo para TX %s. Mensaje: %s", 
                             tx_sudo.reference, error_msg)
                return request.redirect(f'/shop/payment?error={error_msg}')

        except Exception as e:
            _logger.exception("Error en initiate_payment: %s", str(e))
            return request.redirect('/shop/payment?error=processing_error')

    @http.route(_return_url, type='http', auth='public', methods=['GET'], csrf=False)
    def dlocalgo13_return(self, **data):
        _logger.info("=== INICIO DLocalGo13 Return ===")
        _logger.info("Headers recibidos: %s", dict(request.httprequest.headers))
        _logger.info("Datos GET recibidos: %s", pprint.pformat(data))
        _logger.info("URL completa: %s", request.httprequest.url)
        _logger.info("Body completo: %s", request.httprequest.get_data())
        
        # Obtener el acquirer
        acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'dlocalgo13')], limit=1)
        if not acquirer:
            _logger.error("No se encontró el acquirer en el retorno")
            return werkzeug.utils.redirect('/shop/payment?error=acquirer_not_found')

        # Buscar la transacción
        tx_sudo = None
        if data.get('order_id'):
            tx_sudo = request.env['payment.transaction'].sudo().search([('reference', '=', data.get('order_id'))], limit=1)
            _logger.info("Transacción encontrada: %s", tx_sudo.reference if tx_sudo else "No encontrada")
        
        # Si no encontramos la transacción por order_id, buscamos la más reciente pendiente
        if not tx_sudo:
            _logger.info("Buscando transacción más reciente pendiente...")
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('acquirer_id', '=', acquirer.id),
                ('state', 'in', ['pending', 'draft'])
            ], order='id desc', limit=1)
            
            if tx_sudo:
                _logger.info("Encontrada transacción más reciente: %s", tx_sudo.reference)
            else:
                _logger.error("No se encontró ninguna transacción pendiente")
                return werkzeug.utils.redirect('/shop/payment?error=tx_not_found')

        # Consultar el estado actual en DLocalGo usando el acquirer_reference
        if not tx_sudo.acquirer_reference:
            _logger.error("No se encontró referencia de DLocalGo para la transacción %s", tx_sudo.reference)
            return werkzeug.utils.redirect('/shop/payment?error=missing_payment_reference')

        try:
            payment_url = f"{acquirer._dlocalgo13_get_api_url()}/{tx_sudo.acquirer_reference}"
            _logger.info("Consultando estado de pago en DLocalGo: %s", payment_url)
            
            response = requests.get(
                payment_url,
                headers=acquirer._dlocalgo13_get_headers()
            )
            response.raise_for_status()
            payment_status = response.json()
            _logger.info("Estado de pago en DLocalGo (JSON): %s", json.dumps(payment_status, indent=2))
            
            # Actualizar estado de la transacción basado en la respuesta de DLocalGo
            update_vals = {
                'dlocalgo_payment_method': payment_status.get('payment_method_type'),
                'dlocalgo_payer_name': (
                    (payment_status.get('payer', {}).get('first_name', '') + ' ' +
                     payment_status.get('payer', {}).get('last_name', '')).strip()
                    if payment_status.get('payer') else ''
                ),
                'dlocalgo_payer_email': payment_status.get('payer', {}).get('email', '') if payment_status.get('payer') else '',
            }
            if payment_status.get('status') == 'PAID':
                update_vals.update({
                    'state': 'done',
                    'acquirer_reference': payment_status.get('id'),
                    'state_message': 'Pago confirmado por DLocal Go',
                })
                tx_sudo.write(update_vals)
                _logger.info("Valores guardados en la transacción %s: %s", tx_sudo.reference, {k: getattr(tx_sudo, k, None) for k in update_vals.keys()})
                _logger.info("Transacción %s marcada como completada", tx_sudo.reference)
                # Buscar la orden de venta relacionada
                order = request.env['sale.order'].sudo().search([('name', '=', tx_sudo.reference)], limit=1)
                if order:
                    # Asociar la transacción a la orden si no está ya asociada
                    if tx_sudo not in order.transaction_ids:
                        order.write({'transaction_ids': [(4, tx_sudo.id)]})
                        order.invalidate_cache()
                    # Recargar la orden para asegurar la relación actualizada
                    order = request.env['sale.order'].sudo().browse(order.id)
                    # Confirmar la orden si está en estado draft
                    if order.state == 'draft':
                        order.action_confirm()
                        _logger.info("Orden %s confirmada", order.name)
                    # Redirigir a la página de confirmación de la orden
                    return werkzeug.utils.redirect(f'/shop/confirmation?order_id={order.id}')
                else:
                    _logger.warning("No se encontró la orden %s", tx_sudo.reference)
                    return werkzeug.utils.redirect('/shop/payment?error=order_not_found')
            elif payment_status.get('status') == 'PENDING':
                update_vals.update({
                    'state': 'pending',
                    'state_message': 'Pago pendiente en DLocal Go'
                })
                tx_sudo.write(update_vals)
                _logger.info("Transacción %s marcada como pendiente", tx_sudo.reference)
                return werkzeug.utils.redirect('/shop/payment?error=payment_pending')
            else:
                update_vals.update({
                    'state': 'error',
                    'state_message': f"Pago no completado. Estado: {payment_status.get('status')}"
                })
                tx_sudo.write(update_vals)
                _logger.warning("Transacción %s marcada como error. Estado: %s", 
                              tx_sudo.reference, payment_status.get('status'))
                return werkzeug.utils.redirect('/shop/payment?error=payment_failed')
                
        except Exception as e:
            _logger.exception("Error al consultar estado en DLocalGo: %s", str(e))
            # No marcamos como error inmediatamente, dejamos la transacción en su estado actual
            return werkzeug.utils.redirect('/shop/payment?error=status_check_failed')

    @http.route(_cancel_url, type='http', auth='public', methods=['GET'], csrf=False)
    def dlocalgo13_cancel(self, **data):
        _logger.info("=== INICIO DLocalGo13 Cancel ===")
        _logger.info("Headers recibidos: %s", dict(request.httprequest.headers))
        _logger.info("Datos GET recibidos: %s", pprint.pformat(data))
        _logger.info("URL completa: %s", request.httprequest.url)
        
        # Buscar la transacción
        tx_sudo = request.env['payment.transaction'].sudo().search([('reference', '=', data.get('order_id'))], limit=1)
        if tx_sudo:
            tx_sudo.write({
                'state': 'cancel',
                'state_message': 'Pago cancelado por el usuario'
            })
            _logger.info("DLocalGo13: Transacción %s marcada como cancelada", tx_sudo.reference)
        
        _logger.info("=== FIN DLocalGo13 Cancel ===")
        return werkzeug.utils.redirect('/shop/payment')

    @http.route(_webhook_url, type='http', auth='public', methods=['POST'], csrf=False)
    def dlocalgo13_webhook(self, **data):
        _logger.info("=== INICIO DLocalGo13 Webhook ===")
        _logger.info("Headers recibidos: %s", dict(request.httprequest.headers))
        _logger.info("Datos POST recibidos: %s", pprint.pformat(data))
        _logger.info("URL completa: %s", request.httprequest.url)
        _logger.info("Body completo: %s", request.httprequest.get_data())
        _logger.info("Content-Type: %s", request.httprequest.content_type)
        
        # Intentar parsear el body como JSON
        try:
            if request.httprequest.content_type == 'application/json':
                body_json = json.loads(request.httprequest.get_data())
                _logger.info("Body JSON parseado (webhook): %s", json.dumps(body_json, indent=2))
                # Usar el body_json en lugar de data
                data = body_json
        except Exception as e:
            _logger.error("Error al parsear body JSON: %s", str(e))
            return 'KO'
        
        # Obtener el acquirer
        acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'dlocalgo13')], limit=1)
        if not acquirer:
            _logger.error("DLocalGo13: No se encontró el acquirer en el webhook")
            return 'KO'

        # Buscar la transacción por order_id o id de DLocalGo
        tx_sudo = None
        if data.get('order_id'):
            tx_sudo = request.env['payment.transaction'].sudo().search([('reference', '=', data.get('order_id'))], limit=1)
        elif data.get('id'):
            tx_sudo = request.env['payment.transaction'].sudo().search([('acquirer_reference', '=', data.get('id'))], limit=1)
        
        if not tx_sudo:
            _logger.error("DLocalGo13: No se encontró la transacción para order_id=%s o id=%s", 
                         data.get('order_id'), data.get('id'))
            return 'KO'

        _logger.info("DLocalGo13: Transacción encontrada: %s (ID: %s)", tx_sudo.reference, tx_sudo.id)

        # Mapeo de estados de DLocalGo a estados de Odoo
        status_mapping = {
            'PAID': {
                'state': 'done',
                'message': 'Pago confirmado por DLocal Go (webhook)'
            },
            'PENDING': {
                'state': 'pending',
                'message': 'Pago pendiente en DLocal Go (webhook)'
            },
            'REJECTED': {
                'state': 'error',
                'message': 'Pago rechazado por DLocal Go (webhook)'
            },
            'CANCELLED': {
                'state': 'cancel',
                'message': 'Pago cancelado por DLocal Go (webhook)'
            },
            'EXPIRED': {
                'state': 'error',
                'message': 'Pago expirado en DLocal Go (webhook)'
            }
        }

        # Obtener el estado actual
        current_status = data.get('status')
        if not current_status:
            _logger.error("DLocalGo13: No se recibió estado en el webhook para TX %s", tx_sudo.reference)
            return 'KO'

        # Actualizar estado de la transacción
        if current_status in status_mapping:
            new_state = status_mapping[current_status]
            tx_sudo.write({
                'state': new_state['state'],
                'acquirer_reference': data.get('id'),
                'state_message': new_state['message'],
                'dlocalgo_payment_method': data.get('payment_method_type'),
                'dlocalgo_payer_name': (
                    (data.get('payer', {}).get('first_name', '') + ' ' +
                     data.get('payer', {}).get('last_name', '')).strip()
                    if data.get('payer') else ''
                ),
                'dlocalgo_payer_email': data.get('payer', {}).get('email', '') if data.get('payer') else '',
            })
            _logger.info("DLocalGo13: Transacción %s actualizada a estado '%s' vía webhook", 
                        tx_sudo.reference, new_state['state'])

            # Si el pago está completado, buscar y confirmar la orden
            if current_status == 'PAID':
                order = request.env['sale.order'].sudo().search([('name', '=', tx_sudo.reference)], limit=1)
                if order and order.state == 'draft':
                    order.action_confirm()
                    _logger.info("DLocalGo13: Orden %s confirmada vía webhook", order.name)
        else:
            _logger.warning("DLocalGo13: Estado desconocido '%s' recibido en webhook para TX %s", 
                           current_status, tx_sudo.reference)
            tx_sudo.write({
                'state': 'error',
                'state_message': f"Estado desconocido recibido: {current_status}"
            })

        _logger.info("=== FIN DLocalGo13 Webhook ===")
        return 'OK'