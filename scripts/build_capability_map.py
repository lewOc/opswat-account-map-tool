#!/usr/bin/env python3
"""Build a source-backed OPSWAT product capability map.

This intentionally uses local documentation exports, not a live model call.
The map is meant to constrain account-map generation and give Claude evidence
to cite, so the first version favors reliable product/use-case scaffolding plus
retrieved supporting snippets.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path("/Users/lewis/Documents/opswat_docs_full")
DOWNLOADS = ROOT / "opswat_docs_downloads"
CHUNKS = DOWNLOADS / "chunks.jsonl"
FULL_CORE_CHUNKS = DOWNLOADS / "core_mdcore_chunks.jsonl"
FULL_CORE_DIR = ROOT / "core_v5_19_0"

PROJECT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT / "data"
OUTPUTS_DIR = PROJECT / "outputs"


PRODUCT_SEEDS: dict[str, dict[str, Any]] = {
    "mdcore": {
        "product": "MetaDefender Core",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Files, archives, payloads, and content entering critical workflows.",
        "deployment_zones": ["DMZ", "Corporate IT", "Cloud", "OT transfer boundary"],
        "best_fit_use_cases": [
            "Scan and sanitize files before they enter sensitive networks",
            "Provide central file-analysis services for Kiosk, ICAP, MFT, storage, and custom integrations",
            "Detect malware, spoofed file types, sensitive data, vulnerable components, and suspicious behavior",
        ],
        "buyer_problems": [
            "Untrusted files move between users, vendors, cloud apps, and operational environments",
            "Single-engine malware detection is not enough for critical file ingress points",
            "Security teams need one inspection platform reusable across many transfer paths",
        ],
        "threat_paths": [
            "Weaponized attachments and uploads",
            "Malware hidden in archives",
            "Spoofed file extensions",
            "Sensitive data leakage",
            "Unknown malware requiring sandbox detonation",
        ],
        "industries": ["Energy", "Manufacturing", "Government", "Defence", "Healthcare", "Finance"],
        "compliance_drivers": ["NIS2", "IEC 62443", "NERC CIP", "NIST CSF", "ISO 27001"],
        "account_triggers": ["OT file transfer", "secure upload portal", "regulated data", "air-gapped workflows"],
        "search_terms": [
            "deep cdr",
            "proactive dlp",
            "adaptive sandbox",
            "metascan",
            "file type",
            "vulnerability assessment",
            "country of origin",
            "yara",
            "sbom",
        ],
    },
    "mdkiosk": {
        "product": "MetaDefender Kiosk",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Physical media and removable-device ingress points.",
        "deployment_zones": ["Site entrance", "DMZ", "OT staging area", "Air-gapped boundary"],
        "best_fit_use_cases": [
            "Inspect USB and removable media before files reach OT or secure enclaves",
            "Create a controlled media-validation station for vendors, engineers, and contractors",
        ],
        "buyer_problems": [
            "Contractors and engineers need to move files into restricted environments",
            "Removable media remains a practical malware path into OT and air-gapped sites",
        ],
        "threat_paths": ["USB-borne malware", "Unauthorized executable files", "Infected engineering project files"],
        "industries": ["Energy", "Manufacturing", "Defence", "Transport", "Pharma"],
        "compliance_drivers": ["IEC 62443", "NERC CIP", "NIS2", "NIST CSF"],
        "account_triggers": ["USB policy", "contractor access", "plant floor updates", "air-gapped network"],
        "search_terms": ["kiosk", "removable media", "usb", "metadefender core", "media validation"],
    },
    "mdicap": {
        "product": "MetaDefender ICAP Server",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Web, proxy, gateway, and application file-transfer paths using ICAP.",
        "deployment_zones": ["DMZ", "Corporate IT", "Cloud", "Secure web gateway"],
        "best_fit_use_cases": [
            "Scan files moving through web proxies and secure web gateways",
            "Insert malware and CDR controls into application upload/download flows",
        ],
        "buyer_problems": [
            "Users and external parties upload files through web workflows",
            "Existing proxy controls need content-level inspection and sanitization",
        ],
        "threat_paths": ["Malicious web uploads", "Drive-by downloads", "Partner portal file ingress"],
        "industries": ["Finance", "Energy", "Government", "Healthcare", "SaaS"],
        "compliance_drivers": ["NIST CSF", "ISO 27001", "SOC 2", "NIS2"],
        "account_triggers": ["proxy", "secure web gateway", "file upload", "ICAP", "web app"],
        "search_terms": ["icap", "proxy", "reqmod", "respmod", "metadefender core", "scan"],
    },
    "mdmft": {
        "product": "MetaDefender Managed File Transfer",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Business-to-business and internal managed file exchange.",
        "deployment_zones": ["DMZ", "Corporate IT", "Partner exchange", "Cloud"],
        "best_fit_use_cases": [
            "Secure inbound and outbound partner file transfer",
            "Add malware scanning and sanitization to SFTP-style file exchange workflows",
        ],
        "buyer_problems": [
            "Third parties need to exchange files safely and audibly",
            "File-transfer workflows need security controls without breaking operations",
        ],
        "threat_paths": ["Partner-supplied malware", "Outbound sensitive data movement", "Supply-chain file exchange"],
        "industries": ["Energy", "Manufacturing", "Healthcare", "Finance", "Government"],
        "compliance_drivers": ["ISO 27001", "NIST CSF", "NIS2", "SOC 2"],
        "account_triggers": ["SFTP", "third party", "supplier file exchange", "secure transfer"],
        "search_terms": ["managed file transfer", "sftp", "transfer", "user", "package", "metadefender core"],
    },
    "mdss": {
        "product": "MetaDefender Storage Security",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Cloud and enterprise storage repositories.",
        "deployment_zones": ["Cloud", "Corporate IT", "Storage layer"],
        "best_fit_use_cases": [
            "Scan files landing in object storage and shared repositories",
            "Prevent malware persistence in enterprise storage buckets and collaboration platforms",
        ],
        "buyer_problems": [
            "Storage is a landing zone for partner, customer, and employee files",
            "Malware can persist in storage and be downloaded by trusted users later",
        ],
        "threat_paths": ["Malicious storage uploads", "Dormant malware in buckets", "Shared repository contamination"],
        "industries": ["Finance", "Healthcare", "SaaS", "Government", "Energy"],
        "compliance_drivers": ["SOC 2", "ISO 27001", "HIPAA", "NIST CSF"],
        "account_triggers": ["S3", "Azure Blob", "SharePoint", "storage bucket", "object storage"],
        "search_terms": ["storage", "bucket", "aws", "s3", "azure", "blob", "sharepoint"],
    },
    "mdemail": {
        "product": "MetaDefender Email Gateway Security",
        "family": "MetaDefender Platform",
        "priority": 1,
        "what_it_protects": "Inbound and outbound email content and attachments.",
        "deployment_zones": ["Email perimeter", "DMZ", "Cloud mail"],
        "best_fit_use_cases": [
            "Scan, sanitize, and control email attachments before users receive them",
            "Reduce phishing and malware exposure from email-borne files",
        ],
        "buyer_problems": [
            "Email remains a high-volume malware and phishing path",
            "Attachments need content-level controls beyond basic mail filtering",
        ],
        "threat_paths": ["Phishing attachments", "Malicious documents", "Sensitive data leaving by email"],
        "industries": ["Finance", "Healthcare", "Government", "Energy", "Manufacturing"],
        "compliance_drivers": ["ISO 27001", "NIST CSF", "HIPAA", "SOC 2", "NIS2"],
        "account_triggers": ["email gateway", "attachment", "phishing", "Microsoft 365"],
        "search_terms": ["email", "smtp", "attachment", "phishing", "deep cdr", "dlp"],
    },
    "netwall": {
        "product": "MetaDefender Security Gateway",
        "family": "OT and Network Security",
        "priority": 1,
        "what_it_protects": "Network boundaries between IT, DMZ, and OT zones.",
        "deployment_zones": ["IT/OT boundary", "DMZ", "OT Level 3", "Industrial perimeter"],
        "best_fit_use_cases": [
            "Segment and control traffic crossing industrial network boundaries",
            "Add protocol-aware inspection and gateway controls at IT/OT choke points",
        ],
        "buyer_problems": [
            "Flat or poorly controlled IT/OT connectivity increases blast radius",
            "Industrial traffic needs controlled paths, not generic firewall assumptions",
        ],
        "threat_paths": ["IT-to-OT lateral movement", "Unsafe remote/vendor paths", "Uncontrolled industrial protocol flows"],
        "industries": ["Energy", "Manufacturing", "Water", "Transport", "Oil and Gas"],
        "compliance_drivers": ["IEC 62443", "NERC CIP", "NIS2", "NIST CSF"],
        "account_triggers": ["Purdue", "SCADA", "IT OT", "segmentation", "industrial firewall"],
        "search_terms": ["netwall", "security gateway", "modbus", "opc", "industrial", "purdue"],
    },
    "netwalldiode": {
        "product": "MetaDefender Optical Diode",
        "family": "OT and Network Security",
        "priority": 1,
        "what_it_protects": "High-assurance one-way data transfer boundaries.",
        "deployment_zones": ["Air gap", "OT/IT boundary", "High-security enclave"],
        "best_fit_use_cases": [
            "Move operational data out of secure networks without allowing inbound connectivity",
            "Support monitoring and analytics while preserving one-way separation",
        ],
        "buyer_problems": [
            "Operations teams need outbound data visibility without exposing control networks",
            "Critical environments require hardware-enforced separation",
        ],
        "threat_paths": ["Inbound command path into OT", "Bidirectional remote access risk", "Data exfiltration policy breach"],
        "industries": ["Energy", "Defence", "Water", "Nuclear", "Transport"],
        "compliance_drivers": ["NERC CIP", "IEC 62443", "NIS2", "NIST CSF"],
        "account_triggers": ["diode", "unidirectional", "air gap", "historian replication"],
        "search_terms": ["optical diode", "unidirectional", "one-way", "data diode", "historian"],
    },
    "diode_x": {
        "product": "MetaDefender Diode X",
        "family": "OT and Network Security",
        "priority": 2,
        "what_it_protects": "High-assurance unidirectional data exchange for secure environments.",
        "deployment_zones": ["Air gap", "OT/IT boundary", "Secure enclave"],
        "best_fit_use_cases": [
            "Transfer data across highly restricted boundaries with one-way enforcement",
            "Support regulated OT data sharing without opening inbound routes",
        ],
        "buyer_problems": [
            "Security teams need a stronger control than firewall policy for critical boundaries",
            "Operations need outbound visibility from isolated sites",
        ],
        "threat_paths": ["Inbound connectivity to isolated networks", "Misconfigured bidirectional transfer paths"],
        "industries": ["Defence", "Energy", "Nuclear", "Transport", "Government"],
        "compliance_drivers": ["NERC CIP", "IEC 62443", "NIS2"],
        "account_triggers": ["diode x", "data diode", "unidirectional", "air gap"],
        "search_terms": ["diode x", "unidirectional", "data diode", "one-way"],
    },
    "ot": {
        "product": "MetaDefender OT Security",
        "family": "OT and Network Security",
        "priority": 1,
        "what_it_protects": "Industrial assets, control networks, and OT security posture.",
        "deployment_zones": ["OT Level 3", "OT Level 2", "Industrial network"],
        "best_fit_use_cases": [
            "Discover and monitor OT assets and communications",
            "Identify OT cyber risk and support segmentation and vulnerability reduction",
        ],
        "buyer_problems": [
            "OT teams often lack accurate asset inventory and risk visibility",
            "Security teams need industrial context for vulnerabilities and communication flows",
        ],
        "threat_paths": ["Unknown OT assets", "Unsafe industrial communications", "Unmanaged vulnerabilities"],
        "industries": ["Energy", "Manufacturing", "Water", "Transport", "Oil and Gas"],
        "compliance_drivers": ["IEC 62443", "NERC CIP", "NIS2", "NIST CSF"],
        "account_triggers": ["OT", "SCADA", "PLC", "HMI", "asset discovery"],
        "search_terms": ["ot security", "asset", "plc", "scada", "hmi", "vulnerability"],
    },
    "metadefender_ot_access": {
        "product": "MetaDefender OT Access",
        "family": "OT and Network Security",
        "priority": 2,
        "what_it_protects": "Remote and privileged access paths into OT environments.",
        "deployment_zones": ["OT access broker", "DMZ", "Vendor access path"],
        "best_fit_use_cases": [
            "Control and broker remote vendor access into operational environments",
            "Reduce standing access and improve oversight of OT remote sessions",
        ],
        "buyer_problems": [
            "Vendors need access, but persistent remote paths create OT risk",
            "OT teams need visibility and control over privileged remote work",
        ],
        "threat_paths": ["Compromised vendor access", "Unmonitored remote sessions", "Standing credentials into OT"],
        "industries": ["Energy", "Manufacturing", "Water", "Transport"],
        "compliance_drivers": ["IEC 62443", "NERC CIP", "NIS2"],
        "account_triggers": ["remote access", "vendor access", "ot access", "jump server"],
        "search_terms": ["ot access", "remote access", "vendor", "session"],
    },
    "mdmediafirewall": {
        "product": "MetaDefender Media Firewall",
        "family": "MetaDefender Platform",
        "priority": 2,
        "what_it_protects": "Media-transfer workflows into restricted environments.",
        "deployment_zones": ["OT staging area", "Air-gapped boundary", "Secure enclave"],
        "best_fit_use_cases": [
            "Control and inspect media-based file movement into sensitive networks",
            "Bridge operational file transfer needs with malware prevention controls",
        ],
        "buyer_problems": [
            "Physical or semi-offline file movement is hard to govern consistently",
            "Sites need a repeatable process for safe media transfer",
        ],
        "threat_paths": ["Malicious removable media", "Unauthorized file movement", "Unscanned operational updates"],
        "industries": ["Energy", "Manufacturing", "Defence", "Transport"],
        "compliance_drivers": ["IEC 62443", "NERC CIP", "NIS2"],
        "account_triggers": ["media firewall", "removable media", "air gap", "offline transfer"],
        "search_terms": ["media firewall", "media", "removable", "transfer", "scan"],
    },
    "mddrive": {
        "product": "MetaDefender Drive",
        "family": "Endpoint and Incident Response",
        "priority": 2,
        "what_it_protects": "Endpoints and systems that need offline or out-of-band malware assessment.",
        "deployment_zones": ["Endpoint", "Incident response", "Field site"],
        "best_fit_use_cases": [
            "Scan endpoints without installing a persistent agent",
            "Support incident response and forensics triage on suspect systems",
        ],
        "buyer_problems": [
            "Responders need a portable way to assess systems quickly",
            "Some environments cannot accept persistent endpoint agents",
        ],
        "threat_paths": ["Compromised endpoint", "Unknown malware on field systems", "Pre-connection device risk"],
        "industries": ["Energy", "Government", "Healthcare", "Manufacturing"],
        "compliance_drivers": ["NIST CSF", "ISO 27001", "NIS2"],
        "account_triggers": ["offline scan", "portable scan", "incident response", "drive"],
        "search_terms": ["drive", "offline", "scan", "portable", "boot"],
    },
    "mdendpoint": {
        "product": "MetaDefender Endpoint",
        "family": "Endpoint and Access Security",
        "priority": 2,
        "what_it_protects": "Endpoint posture, compliance, and device-risk signals.",
        "deployment_zones": ["Endpoint", "Remote workforce", "Access control"],
        "best_fit_use_cases": [
            "Assess device compliance before access to sensitive systems",
            "Improve endpoint visibility and posture enforcement",
        ],
        "buyer_problems": [
            "Remote and unmanaged devices create access risk",
            "Security teams need posture signals before granting access",
        ],
        "threat_paths": ["Non-compliant endpoint access", "Compromised remote device", "Unmanaged software risk"],
        "industries": ["Finance", "Healthcare", "Government", "SaaS", "Energy"],
        "compliance_drivers": ["SOC 2", "ISO 27001", "NIST CSF", "HIPAA"],
        "account_triggers": ["endpoint", "posture", "device compliance", "remote workforce"],
        "search_terms": ["endpoint", "compliance", "posture", "device", "agent"],
    },
    "filescan": {
        "product": "MetaDefender Aether",
        "family": "Advanced Malware Analysis",
        "priority": 2,
        "what_it_protects": "Unknown and suspicious files requiring dynamic analysis.",
        "deployment_zones": ["Security operations", "File-analysis workflow", "Cloud or private sandbox"],
        "best_fit_use_cases": [
            "Detonate suspicious files for behavioral analysis",
            "Augment file scanning with dynamic malware analysis",
        ],
        "buyer_problems": [
            "Static detection may miss evasive or unknown malware",
            "Analysts need richer behavioral evidence for suspicious files",
        ],
        "threat_paths": ["Unknown malware", "Evasive payloads", "Suspicious attachments and uploads"],
        "industries": ["Finance", "Government", "Energy", "Healthcare", "Manufacturing"],
        "compliance_drivers": ["NIST CSF", "ISO 27001", "SOC 2"],
        "account_triggers": ["sandbox", "detonation", "unknown malware", "aether"],
        "search_terms": ["sandbox", "aether", "dynamic", "behavior", "file analysis"],
    },
    "cm": {
        "product": "My OPSWAT Central Management",
        "family": "Management and Operations",
        "priority": 2,
        "what_it_protects": "Operational management of OPSWAT deployments and estate-wide visibility.",
        "deployment_zones": ["Management plane", "SOC", "Operations"],
        "best_fit_use_cases": [
            "Centrally manage OPSWAT products, policies, inventory, and updates",
            "Give security operations visibility into distributed deployments",
        ],
        "buyer_problems": [
            "Distributed sites need consistent policy and update management",
            "Security teams need operational visibility across product estate",
        ],
        "threat_paths": ["Policy drift", "Outdated security engines", "Unmanaged distributed deployments"],
        "industries": ["Energy", "Manufacturing", "Government", "Healthcare", "Finance"],
        "compliance_drivers": ["ISO 27001", "NIST CSF", "NIS2"],
        "account_triggers": ["central management", "multiple sites", "inventory", "updates"],
        "search_terms": ["central management", "inventory", "policy", "update", "monitoring"],
    },
    "supply_chain": {
        "product": "MetaDefender Software Supply Chain",
        "family": "Software Supply Chain Security",
        "priority": 2,
        "what_it_protects": "Source, build, container, and software package pipelines.",
        "deployment_zones": ["DevSecOps", "CI/CD", "Container registry", "Cloud"],
        "best_fit_use_cases": [
            "Inspect software packages and container images before release or deployment",
            "Support SBOM and software supply-chain risk reduction",
        ],
        "buyer_problems": [
            "Software artifacts can carry malware, vulnerable components, or risky dependencies",
            "Engineering and security teams need artifact-level trust before deployment",
        ],
        "threat_paths": ["Compromised package", "Malicious container image", "Vulnerable dependency"],
        "industries": ["SaaS", "Finance", "Government", "Manufacturing", "Energy"],
        "compliance_drivers": ["NIST CSF", "ISO 27001", "SOC 2"],
        "account_triggers": ["SBOM", "container", "CI/CD", "software supply chain"],
        "search_terms": ["software supply chain", "sbom", "container", "cyclonedx", "spdx", "package"],
    },
    "mdif4p": {
        "product": "MetaDefender Industrial Firewall",
        "family": "OT and Network Security",
        "priority": 2,
        "what_it_protects": "Industrial device and cell/zone network connectivity.",
        "deployment_zones": ["OT Level 1", "OT Level 2", "Industrial cell zone"],
        "best_fit_use_cases": [
            "Protect industrial devices and production cells with ruggedized network controls",
            "Reduce lateral movement at the device or cell level",
        ],
        "buyer_problems": [
            "Industrial assets need network controls suited to plant environments",
            "Cell-level segmentation is difficult with generic enterprise tooling",
        ],
        "threat_paths": ["PLC/HMI lateral movement", "Unsafe industrial protocol access", "Cell-zone compromise"],
        "industries": ["Manufacturing", "Energy", "Water", "Transport"],
        "compliance_drivers": ["IEC 62443", "NIS2", "NERC CIP"],
        "account_triggers": ["industrial firewall", "cell zone", "PLC", "Modbus"],
        "search_terms": ["industrial firewall", "modbus", "plc", "hmi", "interface"],
    },
    "mdcloud": {
        "product": "MetaDefender Cloud",
        "family": "Cloud File Analysis",
        "priority": 3,
        "what_it_protects": "Cloud-based file and hash reputation workflows.",
        "deployment_zones": ["Cloud", "API integration", "Security operations"],
        "best_fit_use_cases": [
            "Use cloud APIs for file, hash, and reputation checks",
            "Prototype or augment file-analysis workflows without hosting every component",
        ],
        "buyer_problems": [
            "Teams need quick access to file reputation and analysis APIs",
            "Custom applications need a cloud file-analysis integration point",
        ],
        "threat_paths": ["Untrusted uploads", "Unknown file reputation", "Custom app file ingress"],
        "industries": ["SaaS", "Finance", "Healthcare", "Government"],
        "compliance_drivers": ["SOC 2", "ISO 27001", "NIST CSF"],
        "account_triggers": ["API", "cloud scanning", "file reputation", "hash"],
        "search_terms": ["cloud", "api", "hash", "scan", "file reputation"],
    },
    "mdndr": {
        "product": "MetaDefender NDR",
        "family": "Network Detection and Response",
        "priority": 3,
        "what_it_protects": "Network traffic visibility and detection workflows.",
        "deployment_zones": ["Network sensor", "SOC", "OT or IT network"],
        "best_fit_use_cases": [
            "Monitor network traffic for detection and investigation",
            "Improve visibility where endpoint controls are insufficient",
        ],
        "buyer_problems": [
            "Security teams need network evidence for threat detection",
            "Industrial or unmanaged environments may lack endpoint telemetry",
        ],
        "threat_paths": ["Lateral movement", "Command and control", "Unusual network behavior"],
        "industries": ["Energy", "Manufacturing", "Government", "Finance"],
        "compliance_drivers": ["NIST CSF", "ISO 27001", "NIS2"],
        "account_triggers": ["NDR", "network detection", "traffic", "sensor"],
        "search_terms": ["ndr", "network detection", "traffic", "sensor", "alert"],
    },
}


CAPABILITY_PATTERNS: dict[str, list[str]] = {
    "Multiscanning": ["multiscanning", "multi-scanning", "metascan", "anti-malware", "antimalware"],
    "Deep CDR": ["deep cdr", "content disarm", "sanitize", "sanitization", "reconstruction"],
    "Proactive DLP": ["proactive dlp", "data loss prevention", "sensitive data", "confidential data"],
    "Adaptive Sandbox": ["adaptive sandbox", "sandbox", "detonation", "behavioral"],
    "File Type Verification": ["file type", "true file type", "extension", "spoofed"],
    "File-Based Vulnerability Assessment": ["file-based vulnerability", "vulnerability assessment"],
    "Threat Intelligence": ["threat intelligence", "reputation", "hash lookup"],
    "Country of Origin": ["country of origin"],
    "YARA Detection": ["yara"],
    "SBOM Analysis": ["software bill of materials", "sbom", "cyclonedx", "spdx"],
    "ICAP Integration": ["icap", "reqmod", "respmod"],
    "Removable Media Control": ["usb", "removable media", "media validation"],
    "Managed File Transfer": ["managed file transfer", "sftp", "secure transfer"],
    "Storage Scanning": ["storage", "bucket", "s3", "azure blob", "sharepoint"],
    "Email Attachment Security": ["email", "smtp", "attachment", "phishing"],
    "Unidirectional Transfer": ["unidirectional", "one-way", "data diode", "optical diode"],
    "Industrial Protocol Control": ["modbus", "opc ua", "dnp3", "s7", "bacnet", "industrial protocol"],
    "OT Asset Visibility": ["asset discovery", "asset inventory", "plc", "scada", "hmi"],
    "Endpoint Posture": ["endpoint", "device compliance", "posture", "agent"],
    "Central Management": ["central management", "inventory", "policy", "monitoring", "update"],
}


PROTOCOL_PATTERNS: dict[str, list[str]] = {
    "ICAP": ["icap", "reqmod", "respmod"],
    "HTTP/HTTPS": ["http", "https", "web"],
    "SMTP": ["smtp", "email"],
    "SMB/CIFS": ["smb", "cifs"],
    "SFTP": ["sftp"],
    "FTP": ["ftp"],
    "REST API": ["api", "rest"],
    "AWS S3": ["s3", "bucket"],
    "Azure Blob": ["azure blob", "blob storage"],
    "SharePoint": ["sharepoint"],
    "OPC UA": ["opc ua", "opc"],
    "Modbus": ["modbus"],
    "DNP3": ["dnp3"],
    "S7": ["s7"],
    "BACnet": ["bacnet"],
    "USB/Removable Media": ["usb", "removable media"],
    "Syslog": ["syslog"],
    "SNMP": ["snmp"],
    "RDP/SSH": ["rdp", "ssh"],
}


CURATED_CAPABILITIES: dict[str, list[str]] = {
    "mdcore": [
        "Multiscanning",
        "Deep CDR",
        "Proactive DLP",
        "Adaptive Sandbox",
        "File Type Verification",
        "File-Based Vulnerability Assessment",
        "Threat Intelligence",
        "Country of Origin",
        "YARA Detection",
        "SBOM Analysis",
    ],
    "mdkiosk": ["Removable Media Control", "Multiscanning", "Deep CDR", "File Type Verification"],
    "mdicap": ["ICAP Integration", "Multiscanning", "Deep CDR", "Proactive DLP", "File Type Verification"],
    "mdmft": ["Managed File Transfer", "Multiscanning", "Deep CDR", "Proactive DLP"],
    "mdss": ["Storage Scanning", "Multiscanning", "Deep CDR", "Proactive DLP", "Adaptive Sandbox"],
    "mdemail": ["Email Attachment Security", "Multiscanning", "Deep CDR", "Proactive DLP"],
    "netwall": ["Industrial Protocol Control", "Network Segmentation", "IT/OT Boundary Control"],
    "netwalldiode": ["Unidirectional Transfer", "Industrial Boundary Protection"],
    "diode_x": ["Unidirectional Transfer", "Industrial Boundary Protection"],
    "ot": ["OT Asset Visibility", "OT Risk Monitoring", "Vulnerability Visibility"],
    "metadefender_ot_access": ["OT Remote Access Control", "Vendor Access Control", "Session Oversight"],
    "mdmediafirewall": ["Removable Media Control", "Multiscanning", "Deep CDR"],
    "mddrive": ["Offline Endpoint Scanning", "Incident Response Triage", "Multiscanning"],
    "mdendpoint": ["Endpoint Posture", "Device Compliance", "Access Risk Signals"],
    "filescan": ["Adaptive Sandbox", "Dynamic Malware Analysis", "Behavioral Analysis"],
    "cm": ["Central Management", "Policy Management", "Inventory", "Monitoring"],
    "supply_chain": ["SBOM Analysis", "Container Image Inspection", "Package Risk Analysis"],
    "mdif4p": ["Industrial Protocol Control", "Cell Zone Segmentation", "Industrial Firewalling"],
    "mdcloud": ["Cloud File Analysis", "Reputation Lookup", "File Scanning API"],
    "mdndr": ["Network Detection and Response", "Traffic Visibility", "Threat Detection"],
}


CURATED_PROTOCOLS: dict[str, list[str]] = {
    "mdcore": ["REST API", "ICAP", "HTTP/HTTPS"],
    "mdkiosk": ["USB/Removable Media", "HTTP/HTTPS", "REST API"],
    "mdicap": ["ICAP", "HTTP/HTTPS"],
    "mdmft": ["SFTP", "FTP", "SMB/CIFS", "HTTP/HTTPS", "REST API"],
    "mdss": ["AWS S3", "Azure Blob", "SharePoint", "SMB/CIFS", "SFTP", "REST API"],
    "mdemail": ["SMTP", "HTTP/HTTPS"],
    "netwall": ["Modbus", "OPC UA", "DNP3", "S7", "BACnet"],
    "netwalldiode": ["OPC UA", "Modbus", "FTP", "SFTP", "Syslog"],
    "diode_x": ["FTP", "SFTP", "Syslog", "HTTP/HTTPS"],
    "ot": ["Modbus", "OPC UA", "DNP3", "S7", "BACnet"],
    "metadefender_ot_access": ["RDP/SSH", "HTTP/HTTPS"],
    "mdmediafirewall": ["USB/Removable Media", "HTTP/HTTPS"],
    "mddrive": ["USB/Removable Media"],
    "mdendpoint": ["HTTP/HTTPS"],
    "filescan": ["REST API", "HTTP/HTTPS"],
    "cm": ["HTTP/HTTPS", "REST API", "Syslog"],
    "supply_chain": ["REST API", "HTTP/HTTPS"],
    "mdif4p": ["Modbus", "OPC UA", "DNP3", "S7", "BACnet"],
    "mdcloud": ["REST API", "HTTP/HTTPS"],
    "mdndr": ["Syslog", "HTTP/HTTPS"],
}


LOW_VALUE_CATEGORIES = {
    "release_notes",
    "release-notes",
    "troubleshooting",
    "knowledge-base",
}

LOW_VALUE_TITLE_TERMS = [
    "release notes",
    "archived release",
    "changelog",
    "error message",
    "scan result codes",
    "standalone db",
    "shared db",
]


CURATED_SOURCE_PRIORITIES: dict[str, list[str]] = {
    "mdcore": [
        "/metascan-engines/437796-metascan-engine-package.md",
        "/deep-cdr/437295-deep-cdr-details.md",
        "/deep-cdr/437275-supported-file-types.md",
        "/proactive-dlp/437700-user-guide.md",
        "/proactive-dlp/437702-detect-sensitive-information.md",
        "/adaptive-sandbox/437856-overview.md",
        "/adaptive-sandbox/437869-enhancing-threat-detection-with-yara-adaptive-sandbox.md",
        "/utilities-engines/437777-file-type-engine.md",
        "/utilities-engines/437773-yara-engine.md",
        "/software-bill-of-materials/437882-overview.md",
        "/country-of-origin/437889-country-of-origin.md",
        "/threat-intelligence-engine/437899-overview.md",
    ],
}


def clean_text(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`+", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def snippet(text: str, limit: int = 360) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def normalize_source_path(path: str) -> str:
    if path.startswith("/private/tmp/opswat_core_ingest/mdcore_v5_19_0"):
        return path.replace(
            "/private/tmp/opswat_core_ingest/mdcore_v5_19_0",
            str(FULL_CORE_DIR),
            1,
        )
    return path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_records() -> list[dict[str, Any]]:
    records = [r for r in load_jsonl(CHUNKS) if r.get("metadata", {}).get("product") != "mdcore"]
    core = load_jsonl(FULL_CORE_CHUNKS)
    for record in core:
        metadata = record.setdefault("metadata", {})
        metadata["product"] = "mdcore"
        metadata["product_name"] = "MetaDefender Core"
        metadata["source_path"] = normalize_source_path(metadata.get("source_path", ""))
    records.extend(core)
    return records


def find_terms(text: str, patterns: dict[str, list[str]]) -> list[str]:
    low = text.lower()
    found = []
    for label, terms in patterns.items():
        if any(term.lower() in low for term in terms):
            found.append(label)
    return sorted(found)


def source_priority_bonus(slug: str, source_path: str) -> int:
    priorities = CURATED_SOURCE_PRIORITIES.get(slug, [])
    for idx, suffix in enumerate(priorities):
        if source_path.endswith(suffix):
            return 80 - idx
    return 0


def score_record(record: dict[str, Any], slug: str, seed: dict[str, Any]) -> tuple[int, list[str], list[str], list[str]]:
    metadata = record.get("metadata", {})
    haystack = " ".join(
        [
            str(metadata.get("title", "")),
            str(metadata.get("category", "")),
            str(metadata.get("header_path", "")),
            str(record.get("text", "")),
        ]
    ).lower()
    search_terms = [t.lower() for t in seed.get("search_terms", [])]
    matched_terms = [term for term in search_terms if term in haystack]
    capabilities = find_terms(haystack, CAPABILITY_PATTERNS)
    protocols = find_terms(haystack, PROTOCOL_PATTERNS)
    score = len(matched_terms) * 12 + len(capabilities) + len(protocols)
    title = str(metadata.get("title", "")).lower()
    category = str(metadata.get("category", "")).lower()
    if any(term in title for term in search_terms):
        score += 10
    if any(term in category for term in search_terms):
        score += 6
    if category in {"overview", "integration", "configuration", "operating", "deep-cdr", "proactive-dlp", "adaptive-sandbox"}:
        score += 4
    if category in LOW_VALUE_CATEGORIES:
        score -= 14
    if any(term in title for term in LOW_VALUE_TITLE_TERMS):
        score -= 18
    if not matched_terms:
        score -= 12
    score += source_priority_bonus(slug, normalize_source_path(str(metadata.get("source_path", ""))))
    if len(clean_text(str(record.get("text", "")))) < 80:
        score -= 5
    return score, capabilities, protocols, matched_terms


def evidence_for_product(records: list[dict[str, Any]], slug: str, seed: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    scored = []
    for record in records:
        metadata = record.get("metadata", {})
        if metadata.get("product") != slug:
            continue
        score, capabilities, protocols, matched_terms = score_record(record, slug, seed)
        if not matched_terms:
            continue
        if score <= 0:
            continue
        scored.append((score, record, capabilities, protocols))

    scored.sort(key=lambda item: item[0], reverse=True)
    evidence = []
    seen_sources = set()
    seen_titles = Counter()
    for score, record, capabilities, protocols in scored:
        metadata = record.get("metadata", {})
        source = normalize_source_path(metadata.get("source_path", ""))
        title = metadata.get("title", "")
        key = (source, title)
        if key in seen_sources:
            continue
        if seen_titles[title] >= 2:
            continue
        seen_sources.add(key)
        seen_titles[title] += 1
        evidence.append(
            {
                "title": title,
                "category": metadata.get("category", ""),
                "source_path": source,
                "header_path": metadata.get("header_path", ""),
                "snippet": snippet(str(record.get("text", ""))),
                "matched_capabilities": capabilities,
                "matched_protocols": protocols,
                "score": score,
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def build_map() -> dict[str, Any]:
    records = load_records()
    by_product = defaultdict(list)
    for record in records:
        by_product[record.get("metadata", {}).get("product")].append(record)

    products = []
    for slug, seed in sorted(PRODUCT_SEEDS.items(), key=lambda item: item[1]["priority"]):
        product_records = by_product.get(slug, [])
        evidence = evidence_for_product(records, slug, seed)
        capabilities = CURATED_CAPABILITIES.get(slug, [])
        protocols = CURATED_PROTOCOLS.get(slug, [])
        confidence = "high" if len(evidence) >= 4 else "medium" if evidence else "low"
        products.append(
            {
                "slug": slug,
                "product": seed["product"],
                "family": seed["family"],
                "priority": seed["priority"],
                "confidence": confidence,
                "chunk_count": len(product_records),
                "what_it_protects": seed["what_it_protects"],
                "deployment_zones": seed["deployment_zones"],
                "best_fit_use_cases": seed["best_fit_use_cases"],
                "buyer_problems": seed["buyer_problems"],
                "threat_paths": seed["threat_paths"],
                "capabilities": capabilities,
                "protocols_and_integrations": protocols,
                "industries": seed["industries"],
                "compliance_drivers": seed["compliance_drivers"],
                "account_triggers": seed["account_triggers"],
                "evidence": evidence,
            }
        )

    return {
        "metadata": {
            "generated_from": {
                "non_core_chunks": str(CHUNKS),
                "core_chunks": str(FULL_CORE_CHUNKS),
                "core_source_folder": str(FULL_CORE_DIR),
            },
            "notes": [
                "MetaDefender Core is loaded from core_mdcore_chunks.jsonl, not the thin mdcore_v5_19_0 folder inside opswat_docs_downloads.",
                "This is a v1 sales/account-mapping capability map. Use it to constrain generation, then improve rows as field feedback arrives.",
            ],
            "product_count": len(products),
        },
        "products": products,
    }


def join_list(values: list[str]) -> str:
    return "; ".join(values)


def write_csv(capability_map: dict[str, Any], path: Path) -> None:
    fields = [
        "slug",
        "product",
        "family",
        "priority",
        "confidence",
        "chunk_count",
        "what_it_protects",
        "deployment_zones",
        "best_fit_use_cases",
        "buyer_problems",
        "threat_paths",
        "capabilities",
        "protocols_and_integrations",
        "industries",
        "compliance_drivers",
        "account_triggers",
        "top_evidence_sources",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for product in capability_map["products"]:
            row = {field: product.get(field, "") for field in fields}
            for field in [
                "deployment_zones",
                "best_fit_use_cases",
                "buyer_problems",
                "threat_paths",
                "capabilities",
                "protocols_and_integrations",
                "industries",
                "compliance_drivers",
                "account_triggers",
            ]:
                row[field] = join_list(product.get(field, []))
            row["top_evidence_sources"] = join_list(
                [
                    f"{e.get('title')} ({e.get('source_path')})"
                    for e in product.get("evidence", [])[:4]
                ]
            )
            writer.writerow(row)


def write_markdown(capability_map: dict[str, Any], path: Path) -> None:
    lines = [
        "# OPSWAT Capability Map",
        "",
        "This v1 map is designed to constrain the account-mapping tool. Each product row includes sales-fit fields plus source-backed evidence.",
        "",
        "> Core note: MetaDefender Core evidence is loaded from the full `core_mdcore_chunks.jsonl` corpus, not the thin `opswat_docs_downloads/mdcore_v5_19_0` folder.",
        "",
    ]
    for product in capability_map["products"]:
        lines.extend(
            [
                f"## {product['product']}",
                "",
                f"- Slug: `{product['slug']}`",
                f"- Family: {product['family']}",
                f"- Confidence: {product['confidence']}",
                f"- Indexed chunks considered: {product['chunk_count']}",
                f"- Protects: {product['what_it_protects']}",
                f"- Deployment zones: {join_list(product['deployment_zones'])}",
                f"- Capabilities detected: {join_list(product['capabilities']) or 'Needs review'}",
                f"- Protocols/integrations detected: {join_list(product['protocols_and_integrations']) or 'Needs review'}",
                "",
                "Best-fit use cases:",
            ]
        )
        lines.extend([f"- {item}" for item in product["best_fit_use_cases"]])
        lines.extend(["", "Buyer problems:"])
        lines.extend([f"- {item}" for item in product["buyer_problems"]])
        lines.extend(["", "Threat paths:"])
        lines.extend([f"- {item}" for item in product["threat_paths"]])
        lines.extend(["", "Industries: " + join_list(product["industries"])])
        lines.extend(["Compliance drivers: " + join_list(product["compliance_drivers"])])
        lines.extend(["Account triggers: " + join_list(product["account_triggers"])])
        lines.extend(["", "Evidence:"])
        for evidence in product.get("evidence", [])[:5]:
            lines.extend(
                [
                    f"- {evidence.get('title')} [{evidence.get('category')}]",
                    f"  - Source: `{evidence.get('source_path')}`",
                    f"  - Snippet: {evidence.get('snippet')}",
                ]
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    capability_map = build_map()
    json_text = json.dumps(capability_map, indent=2, ensure_ascii=False)
    (DATA_DIR / "capability_map.json").write_text(json_text + "\n", encoding="utf-8")
    (OUTPUTS_DIR / "capability_map.json").write_text(json_text + "\n", encoding="utf-8")
    write_csv(capability_map, OUTPUTS_DIR / "capability_map.csv")
    write_markdown(capability_map, OUTPUTS_DIR / "capability_map.md")
    print(f"Products mapped: {len(capability_map['products'])}")
    print(f"Wrote: {OUTPUTS_DIR / 'capability_map.json'}")
    print(f"Wrote: {OUTPUTS_DIR / 'capability_map.csv'}")
    print(f"Wrote: {OUTPUTS_DIR / 'capability_map.md'}")


if __name__ == "__main__":
    main()
