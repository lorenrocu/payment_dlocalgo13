# -*- coding: utf-8 -*-
"""Override de sale.order para evitar que el módulo de secuencia
cambie el número de pedido durante el flujo de pasarela de pago.

Lógica:
1. Detecta el contexto `website_sale_transaction_state` que Odoo
   coloca cuando la pasarela procesa el pago.
2. Si está presente y la orden proviene del sitio web (`website_id`)
   clona `vals`, quita `website_id` temporalmente y crea la orden.
   Así, la secuencia especial `sale.order.web` no se dispara.
3. Una vez creada la orden, restablece el `website_id` original.

De esta forma mantenemos la referencia original `/` que la pasarela
usa para conciliar pagos, sin desactivar el módulo de secuencia
para el resto de casos.
"""
from odoo import api, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model
    def create(self, vals):
        # Si estamos en medio de una confirmación de transacción web, saltamos la secuencia web.
        if self._context.get("website_sale_transaction_state"):
            website_id = vals.get("website_id")
            if website_id:
                # Copiamos vals para no mutar el original fuera de este scope.
                vals = dict(vals)
                vals["website_id"] = False  # Evita disparar la secuencia especial
            order = super(SaleOrder, self).create(vals)
            # Restauramos website_id para que la orden siga ligada al sitio.
            if website_id:
                order.write({"website_id": website_id})
            return order
        # Resto de casos: comportamiento normal.
        return super(SaleOrder, self).create(vals)