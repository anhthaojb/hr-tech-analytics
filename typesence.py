import typesense
import pandas as pd
import os

client = typesense.Client({
    'nodes': [{'host': 'localhost', 'port': '8108', 'protocol': 'http'}],
    'api_key': 'changeme123',
    'connection_timeout_seconds': 5,
})

# Xóa collection cũ nếu có
try:
    client.collections['companies'].delete()
except Exception:
    pass

# Tạo collection
client.collections.create({
    'name': 'companies',
    'fields': [
        {'name': 'id',            'type': 'string'},
        {'name': 'tax_code',      'type': 'string', 'facet': True},
        {'name': 'name_official', 'type': 'string'},
    ],
})

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(
    os.path.join(BASE_DIR, 'company.csv'),
    dtype=str,
    keep_default_na=False,
    encoding='utf-8',
)

docs = [
    {
        'id':            str(i),
        'tax_code':      row['tax'].strip(),
        'name_official': row['company_name'].strip(),
    }
    for i, row in df.iterrows()
]

# Import theo batch
for i in range(0, len(docs), 200):
    client.collections['companies'].documents.import_(
        docs[i:i+200], {'action': 'upsert'}
    )

print(f"✅ Đã load {len(docs)} công ty vào Typesense")