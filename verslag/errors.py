"""De fout die het rekenwerk geeft bij ongeldige invoer of als Moneybird niet wil."""


class VerslagError(Exception):
    """Een fout met een melding voor de penningmeester: ongeldig bestand, verkeerde periode, Moneybird-fout.

    Het script print de melding en stopt; het dashboard toont hem op de pagina.
    """
