import os
import sys
import argparse
import logging
from subprocess import check_output

# max load average to restart service
CRITICAL_LOAD = 20
SERVICES = ("radio.service",)

logger = logging.getLogger("load_avg")
logger.setLevel(logging.INFO)


def setup_logger():
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
	'%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    logger = logging.getLogger('rf')
    logger.setLevel(logging.INFO)


def parse_args(args=sys.argv):
    parser = argparse.ArgumentParser(
        description="Monitor load average and restart service"
    )
    parser.add_argument('-c', '--critical-load', type=int, default=CRITICAL_LOAD,
                        help="critical load which passing causes a service restart")
    parser.add_argument('-s', '--stat', type=int, choices=(1, 5, 15), default=1,
                        help="which uptime stat to use, 1, 5 or 15 min")
    parser.add_argument('-v', '--verbose', action="store_true")

    # map the `stat` input to an index in load average output
    load_ndx = {1: 0, 5: 1, 15: 2}
    args = parser.parse_args()
    args.stat = load_ndx[args.stat]

    return args


def restart_service(services, verbose=False):
    for service in services:
        if verbose:
            logger.info("restarting service")
        check_output(("sudo", "systemctl", "restart", service))


def main(args=sys.argv):
    args = parse_args(args=args)

    avg = os.getloadavg()

    if args.verbose:
        logger.info(f"load average: {avg}")

    # 1, 5 or 15 min load average
    if avg[args.stat] > args.critical_load:
        restart_service(SERVICES, args.verbose)


if __name__ == "__main__":
    setup_logger()
    main()
