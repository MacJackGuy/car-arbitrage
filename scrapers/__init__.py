# SG scrapers
from scrapers.sgcarmart  import SgCarMartScraper
from scrapers.carousell  import CarousellScraper
from scrapers.carro      import CarroScraper
from scrapers.runner     import run_sg_scrapers

# UK scrapers
from scrapers.autotrader    import AutoTraderScraper
from scrapers.carandclassic import CarAndClassicScraper
from scrapers.pistonheads   import PistonHeadsScraper
from scrapers.ebay_motors   import EbayMotorsScraper
from scrapers.uk_runner     import run_uk_scrapers
# JamesEditionScraper — disabled (Cloudflare ASN 9009 ban); file kept for future use

__all__ = [
    # SG
    "SgCarMartScraper", "CarousellScraper", "CarroScraper", "run_sg_scrapers",
    # UK
    "AutoTraderScraper", "CarAndClassicScraper", "PistonHeadsScraper",
    "EbayMotorsScraper", "run_uk_scrapers",
]
