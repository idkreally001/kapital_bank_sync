{
    'name': 'Birbank Business Synchronization',
    'version': '18.0.1.2.0',
    'category': 'Accounting',
    'summary': 'Official Birbank Business Connector',
    'description': """
        Centralized Hub for Birbank Business.
        - Dashboard view for connections
        - One-click Connect & Sync
        - Robust duplicate prevention
    """,
    'author': 'idkreally001',
    'website': 'https://github.com/idkreally001/',
    'depends': ['account_online_synchronization'],
    'data': [
        'views/account_online_link_views.xml',
    ],
    'installable': True,
    'license': 'OEEL-1',
    'application': True,
}