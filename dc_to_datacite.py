import os
import argparse
from copy import deepcopy
from lxml import etree as ET

# Namespaces
DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}
DATACITE_NS = "http://datacite.org/schema/kernel-4"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_LANG = f"{{{XML_NS}}}lang"

# DataCite 4.6 mandatory fields
MANDATORY_FIELDS = ["identifier", "creators", "titles", "publisher", "publicationYear"]

# Convert a single record from Dublin Core into DataCite 4.6
def dc_to_datacite(dc_xml_path, output_path):
    tree = ET.parse(dc_xml_path)
    root = tree.getroot()

    # Extract values from DC 
    identifiers = root.findall(".//dc:identifier", namespaces=DC_NS)
    doi = None
    alternate_ids = []
    for id in identifiers:
        if id.text and "doi" in id.text.lower():
            doi = id.text.replace("https://doi.org/", "").replace("doi:", "")
        else:
            alternate_ids.append(id.text)

    titles = root.findall(".//dc:title", namespaces=DC_NS)
    creators = root.findall(".//dc:creator", namespaces=DC_NS)
    publisher = root.findtext(".//dc:publisher", namespaces=DC_NS)
    dates = root.findall(".//dc:date", namespaces=DC_NS)
    subjects = root.findall(".//dc:subject", namespaces=DC_NS)
    contributors = root.findall(".//dc:contributor", namespaces=DC_NS)
    language = root.findtext(".//dc:language", namespaces=DC_NS)
    resource_type = root.findtext(".//dc:type", namespaces=DC_NS)
    descriptions = root.findall(".//dc:description", namespaces=DC_NS)
    rights = root.findall(".//dc:rights", namespaces=DC_NS)
    formats = root.findall(".//dc:format", namespaces=DC_NS)
    relations = root.findall(".//dc:relation", namespaces=DC_NS)
    coverage = root.findall(".//dc:coverage", namespaces=DC_NS)  # left as description fallback
    sources = root.findall(".//dc:source", namespaces=DC_NS)

    
    # Create the <record> element
    record = ET.Element(f"{{{OAI_NS}}}record", nsmap={None: OAI_NS, "xsi": XSI_NS})

    # Create a new <header> inside the record
    header_dc = root.find(f".//{{{OAI_NS}}}header")
    record_header = ET.SubElement(record, f"{{{OAI_NS}}}header")
    
    # Copy child elements from the original header
    for child in header_dc:
        new_child = ET.SubElement(record_header, child.tag, attrib=child.attrib)
        new_child.text = child.text

    # Create <metadata> element under record
    metadata = ET.SubElement(record, f"{{{OAI_NS}}}metadata")

    # Build the <resource> element under <metadata>
    resource = ET.SubElement(metadata, f"{{{DATACITE_NS}}}resource", nsmap={None: DATACITE_NS})
    resource.set(
        f"{{{XSI_NS}}}schemaLocation",
        f"{DATACITE_NS} http://schema.datacite.org/meta/kernel-4.6/metadata.xsd"
    )

    # Add metadata:
    ## identifier
    if doi:
        ET.SubElement(resource, f"{{{DATACITE_NS}}}identifier", identifierType="DOI").text = doi
    elif alternate_ids:
        alt_id_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}alternateIdentifiers")
        for aid in alternate_ids:
            ET.SubElement(
                alt_id_el,
                f"{{{DATACITE_NS}}}alternateIdentifier",
                alternateIdentifierType="Other"
            ).text = aid

    ## creators
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

    ## titles
    if titles:
        titles_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}titles")
        for t in titles:
            t_val = (t.text or "").strip()
            if not t_val:
                continue
            t_el = ET.SubElement(titles_el, f"{{{DATACITE_NS}}}title")
            t_el.text = t_val
            if XML_LANG in t.attrib:
                t_el.set(XML_LANG, t.attrib[XML_LANG])

    ## publisher
    if publisher and publisher.strip():
        ET.SubElement(resource, f"{{{DATACITE_NS}}}publisher").text = publisher.strip()

    ## dates -> publicationYear and issue Date
    if dates:
        first_date = (dates[0].text or "").strip()
        year = first_date[:4] if len(first_date) >= 4 and first_date[:4].isdigit() else None
        if year:
            ET.SubElement(resource, f"{{{DATACITE_NS}}}publicationYear").text = year

        dates_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}dates")
        for d in dates:
            d_text = (d.text or "").strip()
            if not d_text:
                continue
            d_el = ET.SubElement(dates_el, f"{{{DATACITE_NS}}}date", dateType="Issued")
            d_el.text = d_text

    ## subjects
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

    ## contributors
    if contributors:
        contributors_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}contributors")
        for c in contributors:
            c_text = (c.text or "").strip()
            if not c_text:
                continue
            contr_el = ET.SubElement(
                contributors_el, f"{{{DATACITE_NS}}}contributor", contributorType="Other"
            )
            name_el = ET.SubElement(contr_el, f"{{{DATACITE_NS}}}contributorName")
            name_el.text = c_text
            if XML_LANG in c.attrib:
                name_el.set(XML_LANG, c.attrib[XML_LANG])

    ## language
    if language and language.strip():
        ET.SubElement(resource, f"{{{DATACITE_NS}}}language").text = language.strip()

    ## resourceType
    if resource_type and resource_type.strip():
        ET.SubElement(
            resource, f"{{{DATACITE_NS}}}resourceType", resourceTypeGeneral="Dataset"
        ).text = resource_type.strip()

    ## descriptions
    if descriptions:
        descriptions_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}descriptions")
        for desc in descriptions:
            d_text = (desc.text or "").strip()
            if not d_text:
                continue
            d_el = ET.SubElement(descriptions_el, f"{{{DATACITE_NS}}}description", descriptionType="Abstract")
            d_el.text = d_text
            if XML_LANG in desc.attrib:
                d_el.set(XML_LANG, desc.attrib[XML_LANG])

    ## rights
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

    ## formats
    if formats:
        formats_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}formats")
        for f in formats:
            f_text = (f.text or "").strip()
            if f_text:
                ET.SubElement(formats_el, f"{{{DATACITE_NS}}}format").text = f_text

    ## relatedIdentifiers from relation + source
    if relations or sources:
        related_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}relatedIdentifiers")
        for r in relations + sources:
            r_text = (r.text or "").strip()
            if not r_text:
                continue
            ET.SubElement(
                related_el,
                f"{{{DATACITE_NS}}}relatedIdentifier",
                relatedIdentifierType="URL",
                relationType="References",
            ).text = r_text

    ## coverage -> description(type=Other)
    if coverage:
        descriptions_el = resource.find(f"{{{DATACITE_NS}}}descriptions")
        if descriptions_el is None:
            descriptions_el = ET.SubElement(resource, f"{{{DATACITE_NS}}}descriptions")
        for cov in coverage:
            c_text = (cov.text or "").strip()
            if c_text:
                ET.SubElement(
                    descriptions_el, f"{{{DATACITE_NS}}}description", descriptionType="Other"
                ).text = c_text


    # Warnings for missing mandatory fields 
    for field in MANDATORY_FIELDS:
        if resource.find(f".//{{{DATACITE_NS}}}{field}") is None:
            print(f"Warning: Missing mandatory field '{field}' in {dc_xml_path}")

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ET.ElementTree(record).write(
        output_path,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8"
    )

# Convert the whole folder with XMLs from DublinCore into DataCite 4.6
def bulk_convert_dc_to_datacite(input_folder, output_folder):
    os.makedirs(output_folder, exist_ok=True)
    for filename in os.listdir(input_folder):
        if filename.endswith(".oai_dc.xml"):
            in_path = os.path.join(input_folder, filename)
            clean_id = filename.replace(".oai_dc.xml", "")
            out_filename = f"{clean_id}.oai_datacite.xml"
            out_path = os.path.join(output_folder, out_filename)
            try:
                dc_to_datacite(in_path, out_path)
            except Exception as e:
                print(f"Failed to convert {filename}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Dublin Core XMLs to DataCite 4.6 XMLs")
    parser.add_argument("-i", required=True, help="Input folder containing DC XML files")
    parser.add_argument("-o", required=True, help="Output folder for DataCite XML files")
    args = parser.parse_args()

    if args.i is None or not os.path.isdir(args.i) or args.o is None or not os.path.isdir(args.o):
        parser.print_help()
        exit(1)

    bulk_convert_dc_to_datacite(args.i, args.o)
