import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ----------------------------
# Settings
# ----------------------------

DEFAULT_OUTPUT_NAME = "google_maps_list_export"

# Stores your signed-in browser session.
# Keep this private; do not commit or upload it.
PROFILE_DIR = Path(os.environ.get("MAPS_PROFILE_DIR", "./maps_profile")).expanduser()

# Delay between place pages. Increase if Maps loads slowly.
DELAY_MS = 900


# ----------------------------
# Helper functions
# ----------------------------

def clean(value):
    """Collapse whitespace and safely return a string."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def filename_part(value, fallback="unnamed_list"):
    """Convert text to a readable, filesystem-safe filename component."""
    value = clean(value)
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE)
    return value.strip("._") or fallback


def get_coordinates(url):
    """Extract the place latitude and longitude from a canonical Maps URL."""
    match = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", url)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def emit_progress_place(row):
    """Write one machine-readable place event for the optional local UI."""
    latitude, longitude = get_coordinates(row["google_maps_url"])
    payload = {**row, "latitude": latitude, "longitude": longitude}
    print(
        "MAPS_EXPORT_PLACE " + json.dumps(payload, ensure_ascii=False),
        flush=True,
    )


def get_list_name(page):
    """Read the title of the currently loaded Google Maps list."""
    try:
        heading = page.locator("h1.fontTitleLarge").first
        heading.wait_for(state="visible", timeout=10_000)
        return clean(heading.inner_text())
    except Exception:
        return "unnamed_list"


def get_aria_or_text(page, selector):
    """Try to retrieve a value from a Google Maps information element."""
    try:
        element = page.locator(selector).first

        if element.count() == 0:
            return ""

        aria_label = element.get_attribute("aria-label") or ""
        text = element.inner_text() or ""

        for prefix in ("Address:", "Phone:", "Website:", "Plus code:"):
            if aria_label.startswith(prefix):
                return clean(aria_label[len(prefix):])

        return clean(text) or clean(aria_label)

    except Exception:
        return ""


def extract_rating_and_reviews(container):
    """Find a rating and review count from visible/accessibility labels."""
    try:
        labels = container.locator("[aria-label]").evaluate_all(
            """
            elements => elements
                .map(element => element.getAttribute("aria-label"))
                .filter(Boolean)
            """
        )
    except Exception:
        return "", ""

    rating = ""
    review_count = ""

    for label in labels:
        if not rating:
            rating_match = re.search(
                r"([0-5](?:\.\d)?)\s*stars?",
                label,
                re.IGNORECASE,
            )
            if rating_match:
                rating = rating_match.group(1)

        if not review_count:
            review_match = re.search(
                r"([\d,]+(?:\.\d+[KMB])?)\s*(?:reviews?|ratings?)",
                label,
                re.IGNORECASE,
            )
            if review_match:
                review_count = review_match.group(1)

    return rating, review_count


def extract_category(container):
    """Best-effort extraction of the place category."""
    try:
        return clean(container.locator("button.DkEaL").first.inner_text())
    except Exception:
        return ""


def extract_opening_hours(page, container):
    """
    Extract opening-hours information.

    First retrieves the currently visible status, such as:
      Open · Closes 9 PM

    Then tries to expand the hours panel and capture weekly hours if available.
    """
    opening_hours = ""

    try:
        status = container.get_by_text(
            re.compile(r"^(?:Open|Closed)(?:\s|·|$)", re.IGNORECASE)
        ).first

        if status.count() > 0:
            status_text = clean(status.locator("xpath=..").inner_text())
            opening_hours = status_text or clean(status.inner_text())
    except Exception:
        pass

    try:
        hours_button = container.locator('[data-item-id="oh"]').first

        if hours_button.count() == 0:
            hours_icon = container.locator(
                '[aria-label="Show open hours for the week"]'
            ).first

            if hours_icon.count() == 0:
                return opening_hours

            hours_button = hours_icon.locator(
                'xpath=ancestor::*[@role="button"][1]'
            )

        # This usually contains "Open · Closes ..." or similar.
        aria_label = hours_button.get_attribute("aria-label") or ""
        button_text = hours_button.inner_text() or ""

        if not opening_hours:
            opening_hours = clean(button_text) or clean(aria_label)

        # Attempt to expand the full weekly-hours section.
        try:
            hours_button.click(timeout=3_000)
            page.wait_for_timeout(500)
        except Exception:
            return opening_hours

        table_rows = container.locator("table tr").evaluate_all(
            """
            rows => rows.map(row => {
                const day = row.querySelector("td:first-child")
                    ?.textContent?.trim();
                const hours = row.querySelector('td[role="text"]')
                    ?.getAttribute("aria-label");
                return day && hours ? `${day}: ${hours}` : "";
            }).filter(Boolean)
            """
        )

        if table_rows:
            schedule = " | ".join(table_rows)
            return f"{opening_hours} | {schedule}" if opening_hours else schedule

        # Google Maps may expose expanded hours in an accessible dialog.
        dialogs = container.locator('[role="dialog"]')

        if dialogs.count() > 0:
            # Use the most recently opened dialog.
            dialog = dialogs.last
            dialog_text = clean(dialog.inner_text(timeout=3_000))

            # Only use it if it looks like an hours panel.
            if any(
                day in dialog_text.lower()
                for day in [
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ]
            ):
                return dialog_text

        # Alternate attempt: search page text for individual day/hour rows.
        day_rows = []

        for day in [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]:
            try:
                row = container.get_by_text(day, exact=False).first

                if row.count() > 0:
                    row_text = clean(row.locator("xpath=..").inner_text())

                    if row_text and day.lower() in row_text.lower():
                        day_rows.append(row_text)
            except Exception:
                pass

        if day_rows:
            # Remove duplicate rows while preserving order.
            unique_rows = list(dict.fromkeys(day_rows))
            return " | ".join(unique_rows)

    except Exception:
        pass

    return opening_hours


def extract_place_details(page, url):
    """Open one Maps place page and collect its visible details."""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    heading = page.locator("h1.DUwDvf").last
    heading.wait_for(state="visible", timeout=15_000)

    page.wait_for_timeout(DELAY_MS)

    try:
        name = clean(heading.inner_text(timeout=3_000))
    except Exception:
        name = ""

    detail_panel = page.locator('[role="main"]').filter(has=heading).last
    container = detail_panel if detail_panel.count() > 0 else page

    address = get_aria_or_text(container, '[data-item-id="address"]')
    phone = get_aria_or_text(container, '[data-item-id="phone"]')

    # Some Maps pages use IDs such as: phone:+1 555 123 4567
    if not phone:
        phone = get_aria_or_text(
            container,
            '[data-item-id^="phone:"]',
        )

    website = ""
    try:
        website_element = container.locator(
            '[data-item-id="authority"]'
        ).first

        if website_element.count() > 0:
            website = website_element.get_attribute("href") or ""
    except Exception:
        pass

    rating, review_count = extract_rating_and_reviews(container)
    category = extract_category(container)
    opening_hours = extract_opening_hours(page, container)

    return {
        "name": name,
        "category": category,
        "address": address,
        "rating": rating,
        "review_count": review_count,
        "opening_hours": opening_hours,
        "phone": phone,
        "website": website,
        "google_maps_url": page.url,
    }


def collect_place_urls(page):
    """Find all place links in the currently opened Google Maps list."""
    seen_urls = set()
    unchanged_rounds = 0
    previous_loaded_count = 0
    max_scroll_rounds = 100
    place_row_selector = 'button.SMP2wb[jsaction^="pane.wfvdle"]'

    for _ in range(max_scroll_rounds):
        urls = page.locator('a[href*="/maps/place/"]').evaluate_all(
            """
            elements => elements
                .map(element => element.href)
                .filter(Boolean)
            """
        )

        for url in urls:
            seen_urls.add(url.split("#")[0])

        place_row_count = page.locator(place_row_selector).count()
        loaded_count = max(len(seen_urls), place_row_count)

        if loaded_count == previous_loaded_count:
            unchanged_rounds += 1
        else:
            unchanged_rounds = 0

        previous_loaded_count = loaded_count

        # Stop after several scrolls reveal no additional places.
        if unchanged_rounds >= 4:
            break

        page.evaluate(
            """
            () => {
                const placeItem = document.querySelector(
                    'a[href*="/maps/place/"], '
                    + 'button.SMP2wb[jsaction^="pane.wfvdle"]'
                );

                let element = placeItem;

                while (element) {
                    const isScrollable =
                        element.scrollHeight > element.clientHeight + 100;

                    if (isScrollable) {
                        element.scrollBy({
                            top: Math.max(700, element.clientHeight * 0.8),
                            behavior: "instant"
                        });
                        return;
                    }

                    element = element.parentElement;
                }

                window.scrollBy(0, 900);
            }
            """
        )

        page.wait_for_timeout(1200)

    if seen_urls:
        return sorted(seen_urls)

    place_rows = page.locator(place_row_selector)
    row_identifiers = place_rows.evaluate_all(
        """
        elements => elements
            .map(element => element.getAttribute("jslog"))
            .filter(Boolean)
        """
    )
    row_identifiers = list(dict.fromkeys(row_identifiers))

    for index, identifier in enumerate(row_identifiers, start=1):
        try:
            clicked = False

            for attempt in range(20):
                result = page.evaluate(
                    """
                    ([selector, jslog, resetScroll]) => {
                        const rows = [...document.querySelectorAll(selector)];
                        const row = rows
                            .find(element =>
                                element.getAttribute("jslog") === jslog
                            );

                        if (row) {
                            row.click();
                            return "clicked";
                        }

                        let scrollable = rows[0];

                        while (scrollable) {
                            if (
                                scrollable.scrollHeight
                                > scrollable.clientHeight + 100
                            ) {
                                if (resetScroll) {
                                    scrollable.scrollTop = 0;
                                } else {
                                    scrollable.scrollBy(
                                        0,
                                        Math.max(
                                            700,
                                            scrollable.clientHeight * 0.8
                                        )
                                    );
                                }

                                return "scrolled";
                            }

                            scrollable = scrollable.parentElement;
                        }

                        return "missing";
                    }
                    """,
                    [place_row_selector, identifier, attempt == 0],
                )

                if result == "clicked":
                    clicked = True
                    break

                page.wait_for_timeout(700)

            if not clicked:
                raise RuntimeError("The list row was no longer available.")

            page.wait_for_url(re.compile(r"/maps/place/"), timeout=15_000)
            seen_urls.add(page.url.split("#")[0])
            print(
                f"  Located place {index}/{len(row_identifiers)}",
                end="\r",
                flush=True,
            )
        except Exception as error:
            print(f"\n  Could not open list item {index}: {error}")
        finally:
            if "/maps/place/" in page.url:
                page.go_back(wait_until="domcontentloaded", timeout=60_000)
                page.locator(place_row_selector).first.wait_for(
                    state="visible",
                    timeout=15_000,
                )
                page.wait_for_timeout(300)

    if row_identifiers:
        print()

    return sorted(seen_urls)


def write_txt_file(rows, output_file):
    """Write place details as a clean numbered text list."""
    with open(output_file, "w", encoding="utf-8") as file:

        file.write("\n\n")

        if not rows:
            file.write("No places were exported.\n")
            return

        for index, row in enumerate(rows, start=1):
            name = row["name"] or "Unnamed place"

            file.write(f"{index}. {name}\n")

            if row["category"]:
                file.write(f"   Category: {row['category']}\n")

            if row["address"]:
                file.write(f"   Address: {row['address']}\n")

            if row["rating"]:
                rating_text = f"{row['rating']} / 5"

                if row["review_count"]:
                    rating_text += f" ({row['review_count']} reviews)"

                file.write(f"   Rating: {rating_text}\n")

            if row["opening_hours"]:
                file.write(f"   Hours: {row['opening_hours']}\n")

            if row["phone"]:
                file.write(f"   Phone: {row['phone']}\n")

            if row["website"]:
                file.write(f"   Website: {row['website']}\n")

            if row["google_maps_url"]:
                file.write(f"   Maps: {row['google_maps_url']}\n")

            file.write("\n")


def write_json_file(rows, output_file):
    """Write place details as formatted JSON."""
    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)
        file.write("\n")


def write_csv_file(rows, output_file):
    """Write place details as CSV with one place per row."""
    fieldnames = [
        "name",
        "category",
        "address",
        "rating",
        "review_count",
        "opening_hours",
        "phone",
        "website",
        "google_maps_url",
    ]

    with open(output_file, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    """Parse the small set of export options."""
    parser = argparse.ArgumentParser(
        description="Export the places in one of your Google Maps lists."
    )
    parser.add_argument(
        "list_url",
        nargs="?",
        help="Google Maps list URL. Omit it to paste the URL when prompted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_NAME,
        help=(
            "Output path without an extension "
            f"(default: {DEFAULT_OUTPUT_NAME})"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also create a JSON file.",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also create a CSV file.",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


# ----------------------------
# Main program
# ----------------------------

def main():
    args = parse_args()
    output_prefix = Path(args.output).with_suffix("")
    list_url = args.list_url or input(
        "Paste the Google Maps list share URL: "
    ).strip()

    if not list_url:
        print("A Google Maps list URL is required.")
        sys.exit(2)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
        )

        page = context.pages[0] if context.pages else context.new_page()

        page.goto(
            list_url,
            wait_until="domcontentloaded",
        )

        print("\nGoogle Maps has opened in Chromium.")
        print("Loading the specified list...")
        page.wait_for_timeout(2_000)

        list_name = get_list_name(page)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_base = output_prefix.with_name(
            f"{output_prefix.name}_{filename_part(list_name)}_{timestamp}"
        )
        print(f"List: {list_name}")

        print("\nFinding all places in the selected list...")
        place_urls = collect_place_urls(page)

        if not place_urls:
            print("\nNo place URLs were found.")
            print("Confirm that your saved list is open and visible.")
            context.close()
            sys.exit(1)

        print(f"Found {len(place_urls)} unique place(s).")
        print("Exporting details, including opening hours...\n")

        rows = []
        detail_page = context.new_page()

        for index, url in enumerate(place_urls, start=1):
            try:
                row = extract_place_details(detail_page, url)
                rows.append(row)

                if args.progress_json:
                    emit_progress_place(row)

                display_name = row["name"] or url
                print(f"[{index}/{len(place_urls)}] Exported: {display_name}")

            except Exception as error:
                print(f"[{index}/{len(place_urls)}] Failed: {url}")
                print(f"  Error: {error}")

                rows.append(
                    {
                        "name": "",
                        "category": "",
                        "address": "",
                        "rating": "",
                        "review_count": "",
                        "opening_hours": "",
                        "phone": "",
                        "website": "",
                        "google_maps_url": url,
                    }
                )

        output_base.parent.mkdir(parents=True, exist_ok=True)
        output_paths = [output_base.with_suffix(".txt")]
        write_txt_file(rows, output_paths[0])

        if args.json:
            output_paths.append(output_base.with_suffix(".json"))
            write_json_file(rows, output_paths[-1])

        if args.csv:
            output_paths.append(output_base.with_suffix(".csv"))
            write_csv_file(rows, output_paths[-1])

        print("\nDone.")
        print(f"Exported {len(rows)} place(s).")
        print("Saved:")

        for output_path in output_paths:
            print(f"  {output_path.resolve()}")

        context.close()


if __name__ == "__main__":
    main()