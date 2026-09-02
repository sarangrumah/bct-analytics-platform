{
    "name": "Custom Receipt Async Validate",
    "summary": "Background validate untuk stock.picking besar via queue_job",
    "description": """
Menambah tombol 'Validate (Background)' di stock.picking yang memindahkan
eksekusi button_validate ke queue_job. Cocok untuk receipt dengan ribuan
move_line yang biasanya gagal di browser dengan ERR_EMPTY_RESPONSE karena
proxy/port-forward timeout (vpnkit Docker Desktop, dll).

HTTP response balik instan dengan job UUID; user dapat notifikasi via chatter
saat job selesai (sukses/gagal).
""",
    "author": "Custom Platform",
    "category": "Inventory",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["stock", "queue_job", "mail"],
    "data": [
        "data/queue_job_function_data.xml",
        "views/stock_picking_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
