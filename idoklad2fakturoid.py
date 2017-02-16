#!/usr/bin/env python3
#
# This is a tool for importing invoices from iDoklad to Fakturoid
#
# Fakturoid API docs: http://docs.fakturoid.apiary.io/#introduction/pozadavek
# iDoklad API docs: https://app.idoklad.cz/developer/Help
#
# See idoklad.cz and fakturoid.cz

import sys
import json
import pickle
import requests
import argparse


CACHE_FILE = "idoklad2fakturoid.cache"
MESSAGES = {
    'subject_not_found': "Subject with reg. no. %s not found in Fakturoid. You need to create it first.",
    'request_failed': "%s %s failed with code %s\n\n%s",
    'unknown_payment_method': "Unknown iDoklad payment method code: %s",
}


class FakturoidAPI(object):
    def __init__(self, account_name, email, api_key):
        self.session = requests.Session()
        self.session.auth = (email, api_key)
        self.session.headers.update({'User-Agent': 'iDoklad2Fakturoid (dan.keder@gmail.com)'})

        self.api_url = "https://app.fakturoid.cz/api/v2/accounts/{slug}".format(slug=account_name)

    def get_subjects(self):
        resp = self._api_get("/subjects.json")
        if resp.status_code == 200:
            return resp.json()
        else:
            raise Exception(MESSAGES['request_failed'], "GET", "/subjects.json",
                    resp.status_code, resp.text)

    def create_invoice(self, invoice):
        resp = self._api_post("/invoices.json", invoice)
        if resp.status_code == 201:
            return resp.json()
        else:
            raise Exception(MESSAGES['request_failed'], "POST", "/invoices.json",
                    resp.status_code, resp.text)

    def _api_get(self, path):
        return self.session.get(self.api_url + path)

    def _api_post(self, path, payload):
        return self.session.post(self.api_url + path, json=payload)


def parseargs():
    parser = argparse.ArgumentParser(
            description="Import invoices from iDoklad to Fakturoid",
            add_help=False)
    parser.add_argument("--fakturoid-account",
            type=str,
            metavar="NAME",
            dest="fakturoid_account_name",
            required=True,
            help="Fakturoid account name")
    parser.add_argument("--fakturoid-email",
            type=str,
            metavar="EMAIL",
            dest="fakturoid_email",
            required=True,
            help="Fakturoid email address")
    parser.add_argument("--fakturoid-api-key",
            type=str,
            metavar="API_KEY",
            dest="fakturoid_api_key",
            required=True,
            help="Fakturoid API key")
    parser.add_argument('idoklad_invoices_json',
            type=str,
            metavar="FILE",
            help="JSON file containing data retrieved from the iDoklad's /IssuedInvoices/Expanded API endpoint")
    return parser.parse_args(sys.argv[1:])


def make_fakturoid_payment_method(idoklad_code):
    if idoklad_code == "B":
        return "bank"
    else:
        raise Exception(MESSAGES['unknown_payment_method'], idoklad_code)


def find_fakturoid_subject_id(fakturoid_subjects, registration_no):
    for subject in fakturoid_subjects:
        if subject['registration_no'] == registration_no:  # "IČO"
            return subject['id']
    raise Exception(MESSAGES['subject_not_found'], registration_no)


def convert_invoice(idoklad_invoice, fakturoid_subjects):
    """ Convert iDoklad invoice structure to Fakturoid invoice.
    """
    return {
        'number': idoklad_invoice['DocumentNumber'],
        'variable_symbol': idoklad_invoice['VariableSymbol'],
        'subject_id': find_fakturoid_subject_id(fakturoid_subjects, idoklad_invoice['Purchaser']['IdentificationNumber']),
        'order_number': idoklad_invoice['OrderNumber'],
        'issued_on': idoklad_invoice['DateOfIssue'],
        'taxable_fulfillment_due': idoklad_invoice['DateOfTaxing'],
        'due': idoklad_invoice['Maturity'],
        'note': idoklad_invoice['ItemsTextPrefix'],
        'footer_note': idoklad_invoice['ItemsTextSuffix'],
        'private_note': idoklad_invoice['Note'],
        'bank_account': "/".join(
            idoklad_invoice['MyCompanyDocumentAddress']['AccountNumber'],
            idoklad_invoice['MyCompanyDocumentAddress']['BankNumberCode']
        ),
        'iban': idoklad_invoice['MyCompanyDocumentAddress']['Iban'],
        'swift_bic': idoklad_invoice['MyCompanyDocumentAddress']['Swift'],
        'payment_method': make_fakturoid_payment_method(idoklad_invoice['PaymentOption']['Code']),
        'currency': idoklad_invoice['Currency']['Code'],
        'exchange_rate': idoklad_invoice['ExchangeRate'],
        'language': "en",
        'lines': convert_invoice_lines(idoklad_invoice),
    }


def convert_invoice_lines(idoklad_invoice):
    lines = []
    for item in idoklad_invoice['IssuedInvoiceItems']:
        if item['Code'] == "ZaokPol" and item['TotalPrice'] == 0:
            continue  # Skip artificial rounding item
        lines.append({
            'name': item['Name'],
            'quantity': item['Amount'],
            'unit_price': item['UnitPrice'],
            'vat_rate': item['VatRate'],
        })
    return lines


if __name__ == "__main__":
    args = parseargs()

    # Cache the various things, Fakturoid API limits the number of requests
    try:
        print("Loading Fakturoid API cache")
        cache = pickle.load(open(CACHE_FILE, 'rb'))
    except IOError:
        cache = {}
    except Exception as e:
        print("WARNING: Cache load failed: %s. Continuing anyway.", e)
        cache = {}

    # Connect to Fakturoid API
    fakturoid = FakturoidAPI(
        account_name=args.fakturoid_account_name,
        email=args.fakturoid_email,
        api_key=args.fakturoid_api_key)

    # Get iDoklad invoices
    # TODO: get this directly from the idoklad api
    idoklad_invoices = json.load(open(args.idoklad_invoices_json))

    # Fetch fakturoid subjects
    if 'fakturoid_subjects' not in cache:
        print("Loading subjects from Fakturoid API")
        cache['fakturoid_subjects'] = fakturoid.get_subjects()
        pickle.dump(cache, open(CACHE_FILE, 'wb'))
    fakturoid_subjects = cache['fakturoid_subjects']

    # Create invoices
    for i, idoklad_invoice in enumerate(idoklad_invoices['Data']):
        print("Processing iDoklad invoice", idoklad_invoice['DocumentNumber'])
        fakturoid_invoice = fakturoid.create_invoice(convert_invoice(idoklad_invoice, fakturoid_subjects))
        print("Crated Fakturoid invoice", fakturoid_invoice['number'])

    print("Done.")
