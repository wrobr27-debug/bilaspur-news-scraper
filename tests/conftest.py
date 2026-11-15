"""Shared fixture: builds a small synthetic workbook mirroring the real
registry's sheet structure so loader tests never touch the network or
the real file."""

from __future__ import annotations

import openpyxl
import pytest


def build_workbook(path) -> str:
    wb = openpyxl.Workbook()

    # Media Publishers: (name, category, url, coverage, verif_status, notes)
    ws = wb.active
    ws.title = "Media Publishers"
    ws.append(["Publisher / source", "Category", "Website / section URL",
               "YouTube URL", "Instagram URL", "Facebook URL", "X URL",
               "Coverage / relevance", "Evidence / activity observed",
               "Verification status", "Notes"])
    ws.append(["The Bilasa Times", "Bilaspur-focused publisher",
               "https://thebilasatimes.in/", "", "", "", "",
               "Bilaspur city, Kota", "",
               "Website active; YouTube publisher-linked",
               "Instagram and X not verified"])
    ws.append(["Takhatpur Ki Bhadas / Bilaspur Ki Bhadas",
               "Bilaspur district-focused publisher",
               "https://bilaspurkibhadas.com/", "", "", "", "",
               "Takhatpur and Bilaspur district", "", "Website/social links publisher-linked",
               "Confirm current website domain"])
    ws.append(["Nai Dunia - Bilaspur", "Regional publisher",
               "https://www.naidunia.com/chhattisgarh/bilaspur", "", "", "", "",
               "Bilaspur city and district", "", "Regional publisher; section identified",
               ""])
    ws.append(["Amar Ujala - Bilaspur", "National/regional publisher",
               "https://www.amarujala.com/video/chhattisgarh/bilaspur-chhattisgarh",
               "", "", "", "", "Bilaspur-Chhattisgarh video coverage", "",
               "Activity in target window not established", ""])
    ws.append(["Haribhoomi - Bilaspur", "Regional publisher",
               "https://www.haribhoomi.com/topic/bilaspur-news", "", "", "", "",
               "Bilaspur topic coverage", "", "Current posting cadence pending", ""])
    ws.append(["Dainik Bhaskar", "National/regional publisher",
               "https://www.bhaskar.com/", "", "", "", "",
               "Chhattisgarh and Bilaspur coverage", "",
               "Bilaspur section/account not separately verified", ""])
    ws.append(["INH News", "Regional publisher",
               "https://linktr.ee/inhnews", "", "", "", "",
               "Chhattisgarh regional news", "",
               "Accounts linked via Linktree", ""])
    ws.append(["Sindhu Youth Media", "Publisher lead",
               "https://www.google.com/search?q=Sindhu+Youth+Media+Bilaspur",
               "", "", "", "", "Bilaspur lead", "Directory listing only",
               "Unverified", "Do not activate without direct evidence."])

    ws2 = wb.create_sheet("Institutions & Business")
    ws2.append(["Organisation / institution", "Type", "Website", "X URL",
                "Instagram URL", "Facebook URL", "YouTube URL",
                "Scope / relevance", "Verification status", "Notes"])
    ws2.append(["District Administration, Bilaspur", "District government",
                "https://bilaspur.gov.in/", "", "", "", "",
                "Bilaspur district", "Official X account identified", ""])
    ws2.append(["District Election Officer, Bilaspur", "Election office",
                "https://bilaspur.gov.in/en/deo-portal/", "", "", "", "",
                "Bilaspur district", "Portal links reported; exact accounts pending", ""])
    ws2.append(["South Eastern Coalfields Limited (SECL)", "Public-sector company",
                "https://secl-cil.in/", "", "", "", "",
                "Bilaspur headquarters / regional operations",
                "Several company-listed accounts identified", ""])
    ws2.append(["Guru Ghasidas Vishwavidyalaya", "Central university",
                "https://www.ggu.ac.in/", "", "", "", "",
                "Bilaspur", "Institutional X account identified", ""])
    ws2.append(["Bilaspur Municipal Corporation", "Urban local body",
                "https://bilaspur.gov.in/en/public-utility-category/urban-body/",
                "", "", "", "", "Bilaspur city", "Social handles pending", ""])

    ws3 = wb.create_sheet("Keyword Watchlist")
    ws3.append(["Keyword group", "Terms / aliases to monitor",
                "Coverage purpose", "Usage notes"])
    ws3.append(["Geography - city",
                "Bilaspur; बिलासपुर; Bilaspur Chhattisgarh",
                "City-wide discovery", "Include English and Hindi spellings"])
    ws3.append(["Geography - localities", "Sarkanda; सरकंडा; Torwa; तोरवा",
                "Neighbourhood-level discovery", ""])
    ws3.append(["", "", "", ""])  # blank row - skipped

    ws4 = wb.create_sheet("Additional Monitoring Targets")
    ws4.append(["Monitoring category", "Source / target to monitor", "Source type",
                "Potential news / signals", "Preferred public source locations",
                "Suggested priority", "Registry status",
                "Verification / editorial notes"])
    ws4.append(["Government & Administration",
                "Bilaspur District Administration / Collectorate",
                "District administration", "Official notices",
                "Official website", "High", "To identify and verify", ""])
    ws4.append(["Education", "Guru Ghasidas Vishwavidyalaya (GGV)", "University",
                "Academic notices", "Official website", "High",
                "To identify and verify", ""])
    ws4.append(["Events, Sports & Culture", "Local sports clubs", "Sports organisations",
                "Events", "Social accounts", "P3", "To identify and verify", ""])

    ws5 = wb.create_sheet("Verification Queue")
    ws5.append(["Priority", "Verification task", "Why it matters",
                "Required evidence", "Current state", "Suggested next action"])
    ws5.append(["P0", "Resolve exact official profiles", "Avoid impostor accounts",
                "Website-to-profile link", "Partially complete", "Open each website"])

    wb.save(path)
    return str(path)


@pytest.fixture
def workbook_path(tmp_path):
    return build_workbook(tmp_path / "registry.xlsx")
