#!/usr/bin/env python3
"""Apply security hardening to S3 artifact bucket."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_s3_client():
    """Get S3 client."""
    try:
        import boto3
        return boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    except Exception as e:
        logger.error(f"Failed to create S3 client: {e}")
        sys.exit(1)


def apply_encryption(client, bucket_name: str) -> bool:
    """Apply default AES256 encryption to bucket."""
    try:
        # Set default encryption to AES256
        client.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {
                        'SSEAlgorithm': 'AES256'
                    },
                    'BucketKeyEnabled': True
                }]
            }
        )
        logger.info(f"✅ Applied AES256 encryption to {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to apply encryption: {e}")
        return False


def apply_bucket_policy(client, bucket_name: str) -> bool:
    """Apply security policy to bucket."""
    try:
        # Create policy that:
        # 1. Denies unencrypted uploads
        # 2. Requires TLS/HTTPS
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyUnencryptedObjectUploads",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:PutObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    "Condition": {
                        "StringNotEquals": {
                            "s3:x-amz-server-side-encryption": ["AES256", "aws:kms"]
                        }
                    }
                },
                {
                    "Sid": "DenyInsecureConnections",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*"
                    ],
                    "Condition": {
                        "Bool": {
                            "aws:SecureTransport": "false"
                        }
                    }
                }
            ]
        }

        client.put_bucket_policy(
            Bucket=bucket_name,
            Policy=json.dumps(policy)
        )
        logger.info(f"✅ Applied security policy to {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to apply bucket policy: {e}")
        return False


def apply_lifecycle(client, bucket_name: str) -> bool:
    """Apply lifecycle rules for automatic cleanup."""
    try:
        # Create lifecycle configuration
        lifecycle_config = {
            'Rules': [
                {
                    'ID': 'DeleteOldStripeExports',
                    'Status': 'Enabled',
                    'Prefix': 'stripe/',
                    'Expiration': {
                        'Days': 30
                    }
                },
                {
                    'ID': 'DeleteOldTempFiles',
                    'Status': 'Enabled',
                    'Prefix': 'tmp/',
                    'Expiration': {
                        'Days': 7
                    }
                },
                {
                    'ID': 'TransitionOldExportsToIA',
                    'Status': 'Enabled',
                    'Prefix': 'exports/',
                    'Transitions': [{
                        'Days': 90,
                        'StorageClass': 'STANDARD_IA'
                    }],
                    'Expiration': {
                        'Days': 365
                    }
                }
            ]
        }

        client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        logger.info(f"✅ Applied lifecycle rules to {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to apply lifecycle rules: {e}")
        return False


def apply_versioning(client, bucket_name: str, enable: bool = True) -> bool:
    """Enable or disable versioning on bucket."""
    try:
        status = 'Enabled' if enable else 'Suspended'
        client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': status}
        )
        logger.info(f"✅ {'Enabled' if enable else 'Disabled'} versioning on {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to configure versioning: {e}")
        return False


def apply_public_access_block(client, bucket_name: str) -> bool:
    """Block all public access to bucket."""
    try:
        client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True
            }
        )
        logger.info(f"✅ Blocked public access on {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to block public access: {e}")
        return False


def verify_bucket_config(client, bucket_name: str) -> dict:
    """Verify current bucket configuration."""
    config = {}

    try:
        # Check encryption
        try:
            enc = client.get_bucket_encryption(Bucket=bucket_name)
            config['encryption'] = enc['ServerSideEncryptionConfiguration']['Rules'][0][
                'ApplyServerSideEncryptionByDefault']['SSEAlgorithm']
        except:
            config['encryption'] = 'None'

        # Check versioning
        try:
            ver = client.get_bucket_versioning(Bucket=bucket_name)
            config['versioning'] = ver.get('Status', 'Disabled')
        except:
            config['versioning'] = 'Error'

        # Check lifecycle
        try:
            lc = client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            config['lifecycle_rules'] = len(lc.get('Rules', []))
        except:
            config['lifecycle_rules'] = 0

        # Check public access
        try:
            pa = client.get_public_access_block(Bucket=bucket_name)
            all_blocked = all([
                pa['PublicAccessBlockConfiguration']['BlockPublicAcls'],
                pa['PublicAccessBlockConfiguration']['IgnorePublicAcls'],
                pa['PublicAccessBlockConfiguration']['BlockPublicPolicy'],
                pa['PublicAccessBlockConfiguration']['RestrictPublicBuckets']
            ])
            config['public_access'] = 'Blocked' if all_blocked else 'Allowed'
        except:
            config['public_access'] = 'Unknown'

        # Check policy
        try:
            policy = client.get_bucket_policy(Bucket=bucket_name)
            config['policy'] = 'Present'
        except:
            config['policy'] = 'None'

    except Exception as e:
        logger.error(f"Failed to verify bucket configuration: {e}")

    return config


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Apply security hardening to S3 bucket"
    )

    parser.add_argument(
        "bucket",
        nargs="?",
        help="S3 bucket name (defaults to ARTIFACT_BUCKET env var)"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify current configuration, don't apply changes"
    )
    parser.add_argument(
        "--skip-versioning",
        action="store_true",
        help="Skip enabling versioning"
    )
    parser.add_argument(
        "--skip-lifecycle",
        action="store_true",
        help="Skip applying lifecycle rules"
    )

    args = parser.parse_args()

    # Get bucket name
    bucket_name = args.bucket or os.getenv("ARTIFACT_BUCKET")
    if not bucket_name:
        logger.error("No bucket specified. Use --bucket or set ARTIFACT_BUCKET env var")
        sys.exit(1)

    # Get S3 client
    client = get_s3_client()

    # Verify bucket exists
    try:
        client.head_bucket(Bucket=bucket_name)
        logger.info(f"Found bucket: {bucket_name}")
    except Exception as e:
        logger.error(f"Bucket {bucket_name} not found or not accessible: {e}")
        sys.exit(1)

    if args.verify_only:
        # Just show current configuration
        config = verify_bucket_config(client, bucket_name)
        print(f"\n📊 Current configuration for {bucket_name}:")
        print(f"  Encryption: {config.get('encryption', 'Unknown')}")
        print(f"  Versioning: {config.get('versioning', 'Unknown')}")
        print(f"  Lifecycle Rules: {config.get('lifecycle_rules', 0)}")
        print(f"  Public Access: {config.get('public_access', 'Unknown')}")
        print(f"  Bucket Policy: {config.get('policy', 'Unknown')}")
    else:
        print(f"\n🔒 Hardening bucket: {bucket_name}\n")

        success = True

        # Apply encryption
        if not apply_encryption(client, bucket_name):
            success = False

        # Apply bucket policy
        if not apply_bucket_policy(client, bucket_name):
            success = False

        # Apply lifecycle rules
        if not args.skip_lifecycle:
            if not apply_lifecycle(client, bucket_name):
                success = False

        # Enable versioning
        if not args.skip_versioning:
            if not apply_versioning(client, bucket_name, enable=True):
                success = False

        # Block public access
        if not apply_public_access_block(client, bucket_name):
            success = False

        # Verify final configuration
        print("\n📊 Final configuration:")
        config = verify_bucket_config(client, bucket_name)
        print(f"  Encryption: {config.get('encryption', 'Unknown')}")
        print(f"  Versioning: {config.get('versioning', 'Unknown')}")
        print(f"  Lifecycle Rules: {config.get('lifecycle_rules', 0)}")
        print(f"  Public Access: {config.get('public_access', 'Unknown')}")
        print(f"  Bucket Policy: {config.get('policy', 'Unknown')}")

        if success:
            print(f"\n✅ Successfully hardened bucket {bucket_name}")
        else:
            print(f"\n⚠️  Some hardening steps failed. Check logs above.")
            sys.exit(1)


if __name__ == "__main__":
    main()