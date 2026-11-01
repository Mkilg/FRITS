"""The game in everyday FRITS: paperwork becomes cards, the Kassensturz scores them.

- stand.py: the game's own tables in frits.db (cards, the Salzkorn ledger, what Fritte owns)
- gewuerze.py: spices, the special cards that reward good bookkeeping
- kassensturz.py: scoring the waiting cards, step by step, for the show

The game only ever *reads* the bookkeeping tables (invoices, expenses, ...). It never
changes them, and nothing in the game can make an invoice or a number appear.
"""
