import argparse
import json
import os

import xmltodict
from pathlib import Path
from multiprocessing import Pool, cpu_count

def transform_record(filepath: Path, output_dir: Path):

    with open(filepath) as f:
        converted = xmltodict.parse(f.read())

    with open(f'{output_dir}/{filepath.name}.json', 'w') as f2:
        f2.write(json.dumps(converted))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', help='input directory', type=str)
    parser.add_argument('-o', help='output directory', type=str)

    args = parser.parse_args()

    if args.i is None or not os.path.isdir(args.i) or args.o is None or not os.path.isdir(args.o):
        parser.print_help()
        exit(1)

    files: list[Path] = (list(Path(args.i).rglob("*.xml")))

    with Pool(processes=cpu_count()) as p:
        p.starmap(transform_record, map(lambda file: (file, args.o), files))