#!/usr/bin/env python3
import os
import argparse
from copy import deepcopy
from lxml import etree as ET

# Namespaces
DDI_NS = {"ddi": "ddi:codebook:2_5"}  # DDI 2.5 codebook
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DATACITE_NS = "http://datacite.org/schema/kernel-4"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_LANG = f"{{{XML_NS}}}lang"

MANDATORY_FIELDS = ["identifier", "creators", "titles", "publisher", "publicationYear"]

def ddi25_to_datacite(ddi_xml_path, output_path):
    tree = ET.parse(ddi_xml_path)
    root = tree.getroot()

    # Extract DDI fields
    identifiers = root.findall(".//ddi:IDNo", namespaces=DDI_NS)
    doi = None
    alternate_ids = []
    for ident in identifiers:
        val = (ident.text or "").strip()
        if not val:
            continue
        agency = ident.attrib.get("agency", "").lower()
        if agency == "doi":
            doi = val
        else:
            alternate_ids.append(val)

    titles = root.findall(".//ddi:titl", namespaces=DDI_NS)
    creators = root.findall(".//ddi:AuthEnty", namespaces=DDI_NS)
    publisher = root.findtext(".//ddi:distrbtr", namespaces=DDI_NS)
    dates = root.findall(".//ddi:distDate", namespaces=DDI_NS)
    subjects = root.findall(".//ddi:topcClas", namespaces=DDI_NS)
    abstracts = root.findall(".//ddi:abstract", namespaces=DDI_NS)
    coverage = root.findall(".//ddi:nation", namespaces=DDI_NS)
    rights = root.findall(".//ddi:restrctn", namespaces=DDI_NS)
    formats = root.findall(".//ddi:fileName", namespaces=DDI_NS)

    # Get header from OAI wrapper
    header_orig = root.find(f".//{{{OAI_NS}}}header")
    if header_orig is None:
        raise ValueError(f"No <header> element found in {ddi_xml_path}")

    record = ET.Element(f"{{{OAI_NS}}}record", nsmap={None: OAI_NS, "xsi": XSI_NS})
    record_header = ET.SubElement(record, f"{{{OAI_NS}}}header")

    for child in header_orig:
        new_child = ET.SubElement(record_header, child.tag, attrib=child.attrib)
        new_child.text = child.text

    metadata_el = ET.SubElement(record, f"{{{OAI_NS}}}metadata")

    resource = ET.SubElement(
        metadata_el,
        f"{{{DATACITE_NS}}}resource",
        nsmap={None: DATACITE_NS, "xsi": XSI_NS},
    )
    resource.set(
        f"{{{XSI_NS}}}schemaLocation",
        f"{DATACITE_NS} http://schema.datacite.org/meta/kernel-4.6/metadata.xsd",
    )

    # identifier
    if doi:
        ET.SubElement(resource, f"{{{DATACITE_NS}}}identifier", identifierType="DOI").text = doi
    elif alternate_ids:
        alt_id_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}alternateIdentifiers")
        for aid in alternate_ids:
            ET.SubElement(
                alt_id_el,
                f"{{{DATACITE_NS}}}alternateIdentifier",
                alternateIdentifierType="Other",
            ).text = aid

    # creators
    if creators:
        creators_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}creators")
        for cr in creators:
            name_text = (cr.text or "").strip()
            if not name_text:
                continue
            cr_el = ET.SubElement(creators_el, f"{{{DATACITE_NS}}}creator")
            name_el = ET.SubElement(cr_el, f"{{{DATACITE_NS}}}creatorName")
            name_el.text = name_text
            if XML_LANG in cr.attrib:
                name_el.set(XML_LANG, cr.attrib[XML_LANG])

    # titles
    # titles
    if titles:
        titles_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}titles")
        seen_titles = set()
        for t in titles:
            t_val = (t.text or "").strip()
            if not t_val:
                continue
            # Build a key: value + lang (if any)
            lang = t.attrib.get(XML_LANG, "")
            key = (t_val, lang)
            if key in seen_titles:
                continue  # skip duplicates
            seen_titles.add(key)

            t_el = ET.SubElement(titles_el, f"{{{DATACITE_NS}}}title")
            t_el.text = t_val
            if lang:
                t_el.set(XML_LANG, lang)


    # publisher
    if publisher and publisher.strip():
        ET.SubElement(resource, f"{{{DATACITE_NS}}}publisher").text = publisher.strip()

    # dates
    if dates:
        first_date = dates[0].attrib.get("date") or (dates[0].text or "").strip()
        year = first_date[:4] if first_date and len(first_date) >= 4 and first_date[:4].isdigit() else None
        if year:
            ET.SubElement(resource, f"{{{DATACITE_NS}}}publicationYear").text = year

        dates_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}dates")
        for d in dates:
            d_val = d.attrib.get("date") or (d.text or "").strip()
            if not d_val:
                continue
            d_el = ET.SubElement(dates_el, f"{{{DATACITE_NS}}}date", dateType="Issued")
            d_el.text = d_val

    # subjects
    if subjects:
        subjects_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}subjects")
        for subj in subjects:
            s_text = (subj.text or "").strip()
            if not s_text:
                continue
            s_el = ET.SubElement(subjects_el, f"{{{DATACITE_NS}}}subject")
            s_el.text = s_text
            if XML_LANG in subj.attrib:
                s_el.set(XML_LANG, subj.attrib[XML_LANG])

    # abstracts
    if abstracts:
        descriptions_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}descriptions")
        for abs_el in abstracts:
            a_text = (abs_el.text or "").strip()
            if not a_text:
                continue
            d_el = ET.SubElement(
                descriptions_el,
                f"{{{DATACITE_NS}}}description",
                descriptionType="Abstract"
            )
            d_el.text = a_text
            if XML_LANG in abs_el.attrib:
                d_el.set(XML_LANG, abs_el.attrib[XML_LANG])

    # coverage → geoLocations
    if coverage:
        geo_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}geoLocations")
        for cov in coverage:
            c_text = (cov.text or "").strip()
            if c_text:
                g_el = ET.SubElement(geo_el, f"{{{DATACITE_NS}}}geoLocation")
                place_el = ET.SubElement(g_el, f"{{{DATACITE_NS}}}geoLocationPlace")
                place_el.text = c_text
                if XML_LANG in cov.attrib:
                    place_el.set(XML_LANG, cov.attrib[XML_LANG])

    # rights
    if rights:
        rights_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}rightsList")
        for r in rights:
            r_text = (r.text or "").strip()
            if not r_text:
                continue
            r_el = ET.SubElement(rights_el, f"{{{DATACITE_NS}}}rights")
            r_el.text = r_text
            if XML_LANG in r.attrib:
                r_el.set(XML_LANG, r.attrib[XML_LANG])

    # formats (file names)
    if formats:
        formats_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}formats")
        for f in formats:
            f_text = (f.text or "").strip()
            if f_text:
                ET.SubElement(formats_el, f"{{{DATACITE_NS}}}format").text = f_text

    # ---------- Missing mandatory fields warnings ----------
    for field in MANDATORY_FIELDS:
        if resource.find(f".//{{{DATACITE_NS}}}{field}") is None:
            print(f"Warning: Missing mandatory field '{field}' in {ddi_xml_path}")

    # ---------- Save ----------
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ET.ElementTree(record).write(
        output_path,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )

def bulk_convert_ddi25_to_datacite(input_folder, output_folder):
    print("Hello")
    os.makedirs(output_folder, exist_ok=True)
    for filename in os.listdir(input_folder):
        if filename.endswith(".oai_ddi25.xml"):
            in_path = os.path.join(input_folder, filename)
            clean_id = filename.replace(".oai_ddi25.xml", "")
            out_filename = f"{clean_id}.oai_datacite.xml"
            out_path = os.path.join(output_folder, out_filename)
            try:
                ddi25_to_datacite(in_path, out_path)
            except Exception as e:
                print(f"Failed to convert {filename}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert DDI 2.5 OAI-PMH XMLs to OAI-PMH + DataCite 4.6 XMLs")
    parser.add_argument("-i", required=True, help="Input folder containing DDI 2.5 XML files")
    parser.add_argument("-o", required=True, help="Output folder for DataCite XML files")
    args = parser.parse_args()

    if args.i is None or not os.path.isdir(args.i) or args.o is None or not os.path.isdir(args.o):
        parser.print_help()
        exit(1)

    bulk_convert_ddi25_to_datacite(args.i, args.o)
