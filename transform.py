import argparse
import json
import os
from utils.normalize_datacite_json import normalize_datacite_json

import xmltodict
from pathlib import Path
from multiprocessing import Pool, cpu_count


def transform_record(filepath: Path, output_dir: Path, normalize: bool):
    with open(filepath) as f:
        converted = xmltodict.parse(f.read(), process_namespaces=True)

    with open(f'{output_dir}/{filepath.name}.json', 'w') as f2:
        if normalize:

            metadata = converted['http://www.openarchives.org/OAI/2.0/:record'][
                'http://www.openarchives.org/OAI/2.0/:metadata']

            if 'http://datacite.org/schema/kernel-4:resource' in metadata:
                resource = metadata['http://datacite.org/schema/kernel-4:resource']
            else:
                # HAL
                resource = metadata['http://www.openarchives.org/OAI/2.0/:resource']

            f2.write(json.dumps(normalize_datacite_json(resource)))
        else:
            f2.write(json.dumps(converted))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', help='input directory', type=str)
    parser.add_argument('-o', help='output directory', type=str)
    parser.add_argument('-n', help='If set, output JSON is normalized', action='store_true')

    args = parser.parse_args()

    if args.i is None or not os.path.isdir(args.i) or args.o is None or not os.path.isdir(args.o):
        parser.print_help()
        exit(1)

    files: list[Path] = (list(Path(args.i).rglob("*.xml")))

    with Pool(processes=cpu_count()) as p:
        p.starmap(transform_record, map(lambda file: (file, args.o, args.n), files))
