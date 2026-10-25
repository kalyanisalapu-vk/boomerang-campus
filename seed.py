import sqlite3
import json
import os
import urllib.request

def run(db_path, uploads_dir):
    os.makedirs(uploads_dir, exist_ok=True)
    items_data = [
        {
            "name": "Black Leather Wallet",
            "category": "Wallet",
            "date": "2024-10-20",
            "place": "Canteen",
            "description": "Black leather bi-fold wallet lost during lunch hours.",
            "photo_url": "https://images.unsplash.com/photo-1627123424574-724758594e93?q=80&w=600&auto=format&fit=crop",
            "features": [
                {"q": "Colour", "a": "Black"},
                {"q": "Brand", "a": "Tommy Hilfiger"},
                {"q": "What is inside (cards/cash)", "a": "SBI debit card and 500 note"}
            ],
            "status": "lost"
        },
        {
            "name": "College ID Card",
            "category": "ID Card",
            "date": "2024-10-22",
            "place": "Central Library",
            "description": "B.Tech CSE student ID card lost in study hall.",
            "photo_url": "https://images.unsplash.com/photo-1611186871348-b1ce696e52c9?q=80&w=600&auto=format&fit=crop",
            "features": [
                {"q": "ID number (last 3 digits)", "a": "456"},
                {"q": "Lanyard / holder colour", "a": "Navy Blue"}
            ],
            "status": "lost"
        },
        {
            "name": "AirPods Pro Case",
            "category": "Earphones",
            "date": "2024-10-18",
            "place": "Seminar Hall",
            "description": "White charging case with earbud inside.",
            "photo_url": "https://images.unsplash.com/photo-1606220588913-b3aacb4d2f46?q=80&w=600&auto=format&fit=crop",
            "features": [
                {"q": "Brand", "a": "Apple"},
                {"q": "Case colour / scratch", "a": "Tiny scratch near charging port"}
            ],
            "status": "matched"
        },
        {
            "name": "Wildcraft Backpack",
            "category": "Bag",
            "date": "2024-10-21",
            "place": "Sports Complex",
            "description": "Blue backpack with laptop compartment.",
            "photo_url": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?q=80&w=600&auto=format&fit=crop",
            "features": [
                {"q": "Brand", "a": "Wildcraft"},
                {"q": "Special mark / keychain", "a": "Batman metallic keychain attached"}
            ],
            "status": "returned"
        }
    ]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for item in items_data:
        cur.execute("SELECT id FROM items WHERE name=?", (item['name'],))
        if cur.fetchone():
            continue

        filename = f"{item['name'].replace(' ', '_').lower()}.jpg"
        filepath = os.path.join(uploads_dir, filename)

        if not os.path.exists(filepath):
            try:
                req = urllib.request.Request(item['photo_url'], headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response, open(filepath, 'wb') as out_file:
                    out_file.write(response.read())
            except Exception as e:
                print(f"Skipping download for {item['name']}: {e}")
                filename = None

        cur.execute("""
            INSERT INTO items (owner_id, name, category, date, place, description, photo, features, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            2,  # Demo user
            item['name'],
            item['category'],
            item['date'],
            item['place'],
            item['description'],
            filename if filename and os.path.exists(os.path.join(uploads_dir, filename)) else None,
            json.dumps(item['features']),
            item['status']
        ))

    conn.commit()
    conn.close()
    print("Database seeded with sample records.")