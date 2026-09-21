"""Authoritative v1.1 taxonomy and finalized Seattle offense decisions.

Promoted from the reviewed v1_1_data_quality_qa decision table.
Keys are normalized (offense_sub_category, nibrs_offense_code).
The reviewed 999/not_a_crime finding is enforced by the semantic source field
in apply_crime_classification, not by a numeric-code exclusion.
"""

CRIMES_AGAINST_PERSONS = "crimes against persons"
CRIMES_AGAINST_PROPERTY = "crimes against property"
CRIMES_AGAINST_SOCIETY = "crimes against society / other"

CANONICAL_CRIME_TYPES = [
    CRIMES_AGAINST_PERSONS,
    CRIMES_AGAINST_PROPERTY,
    CRIMES_AGAINST_SOCIETY,
]


CRIME_CLASSIFICATION_DECISIONS = {
    ('property offenses (includes stolen, destruction)', '290'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'Destruction, Damage, or Vandalism of Property is classified as a Crime Against Property.',
    },
    ('assault offenses', '13b'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Simple Assault as a Crime Against Person.',
    },
    ('all other', '90z'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'All Other Offenses is a catch-all NIBRS category for offenses not otherwise classified and is retained in the Crimes Against Society / Other dashboard category.',
    },
    ('assault offenses', '13c'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Intimidation as a Crime Against Person.',
    },
    ('narcotic violations (includes drug equip.)', '35a'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Drug/Narcotic Violations as a Crime Against Society.',
    },
    ('trespass', '90j'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Trespass of Real Property as a Crime Against Society.',
    },
    ('violation of no contact order', '500'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'Violation of a No Contact Order does not represent a property offense and is retained in the Crimes Against Society / Other dashboard category.',
    },
    ('dui', '90d'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Driving Under the Influence as a Crime Against Society.',
    },
    ('weapon law violation', '520'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Weapon Law Violations as Crimes Against Society.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26b'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Credit Card / Automated Teller Machine Fraud as a Crime Against Property.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26f'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Identity Theft as a Crime Against Property.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26a'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies False Pretenses / Swindle / Confidence Game as a Crime Against Property.',
    },
    ('property offenses (includes stolen, destruction)', '280'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Stolen Property Offenses as Crimes Against Property.',
    },
    ('narcotic violations (includes drug equip.)', '35b'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Drug Equipment Violations as a Crime Against Society.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26c'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Impersonation as a Crime Against Property.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26e'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Wire Fraud as a Crime Against Property.',
    },
    ('disorderly conduct & vagrancy violations', '90c'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Disorderly Conduct as a Crime Against Society.',
    },
    ('kidnapping/abduction', '100'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Kidnapping / Abduction as a Crime Against Person.',
    },
    ('sex offenses', '11d'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Criminal Sexual Contact / Fondling as a Crime Against Person.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '210'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Extortion / Blackmail as a Crime Against Property.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '250'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Counterfeiting / Forgery as a Crime Against Property.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26g'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Hacking / Computer Invasion as a Crime Against Property.',
    },
    ('non-violent family offenses', '90f'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Family Offenses, Nonviolent as Crimes Against Society.',
    },
    ('pornography', '370'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Pornography / Obscene Material as a Crime Against Society.',
    },
    ('animal cruelty', '720'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Animal Cruelty as a Crime Against Society.',
    },
    ('unknown', '-'): {
        "action": 'exclude',
        "target_category": None,
        "reason": 'The record has no usable offense classification and should not contribute to classified crime analysis.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '270'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Embezzlement as a Crime Against Property.',
    },
    ('liquor law violations & drunkenness', '90g'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Liquor Law Violations as a Crime Against Society.',
    },
    ('prostitution offenses', '40c'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Purchasing Prostitution as a Crime Against Society.',
    },
    ('human trafficking', '64a'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Human Trafficking, Commercial Sex Acts as a Crime Against Person.',
    },
    ('disorderly conduct & vagrancy violations', '90b'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Curfew / Loitering / Vagrancy Violations as Crimes Against Society.',
    },
    ('prostitution offenses', '40b'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Assisting or Promoting Prostitution as a Crime Against Society.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '90a'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": "Bad Checks represents a property/economic offense. Although 90A is a historical NIBRS code, it is most appropriately grouped with Property Crime for the dashboard's analytical categories.",
    },
    ('sex offenses', '36b'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Statutory Rape as a Crime Against Person.',
    },
    ('prostitution offenses', '40a'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Prostitution as a Crime Against Society.',
    },
    ('liquor law violations & drunkenness', '90e'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Drunkenness as a Crime Against Society.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '26d'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Welfare Fraud as a Crime Against Property.',
    },
    ('justifiable homicide', '09c'): {
        "action": 'exclude',
        "target_category": None,
        "reason": "NIBRS identifies Justifiable Homicide as 'Not a Crime', so it should be excluded from crime totals and analysis.",
    },
    ('sex offenses', '90h'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Peeping Tom as a Crime Against Society.',
    },
    ('gambling offenses', '39a'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Betting / Wagering as a Crime Against Society.',
    },
    ('sex offenses', '36a'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PERSONS,
        "reason": 'NIBRS classifies Incest as a Crime Against Person.',
    },
    ('gambling offenses', '39c'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Operating / Promoting / Assisting Gambling as a Crime Against Society.',
    },
    ('extortion/fraud/forgery/bribery (includes bad checks)', '510'): {
        "action": 'reclassify',
        "target_category": CRIMES_AGAINST_PROPERTY,
        "reason": 'NIBRS classifies Bribery as a Crime Against Property.',
    },
    ('gambling offenses', '39b'): {
        "action": 'retain',
        "target_category": CRIMES_AGAINST_SOCIETY,
        "reason": 'NIBRS classifies Operating / Promoting / Assisting Gambling as a Crime Against Society.',
    },
}
