# EN 16931 validation for CII (e-invoices)

`EN16931-CII-validation.xslt` is the official compiled Schematron for EN 16931 in the
UN/CEFACT CII syntax, from the European Commission's eInvoicing-EN16931 project,
release validation-1.3.14 (Schematron 1.3.13):
https://github.com/ConnectingEurope/eInvoicing-EN16931/releases/tag/validation-1.3.14

Licensed under the European Union Public Licence (EUPL) 1.2. FRITS only uses it in tests
(tests/test_erechnung.py) to check every e-invoice against the business rules, locally and
without a Java validator. It needs the dev dependency `saxonche` (XSLT 2.0).
