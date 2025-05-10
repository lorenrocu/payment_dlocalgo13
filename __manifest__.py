# -*- coding: utf-8 -*-
{
    'name': 'DLocal Go 13 Payment Acquirer',
    'version': '13.0.1.0.0',
    'category': 'Accounting/Payment Acquirers',
    'summary': 'Integrates DLocal Go Payment Gateway with Odoo 13 eCommerce',
    'author': 'Lorenzo Romero',
    'website': 'https://helydev.com',
    'depends': [
        'payment',         # Dependencia base para adquirentes [cite: 173]
        'website_sale',    # Dependencia para integración con eCommerce [cite: 173]
    ],
    'data': [
        'views/payment_acquirer_views.xml',
        'views/payment_templates.xml', # Si creas una plantilla de botón personalizada
        'data/payment_acquirer_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3', # O la licencia que aplique
}