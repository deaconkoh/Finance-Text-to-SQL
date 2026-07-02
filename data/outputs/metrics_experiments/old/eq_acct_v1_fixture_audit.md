# Eq_acct_v1 Fixture Audit

## Overall Row Counts

| Metric | Value |
| --- | --- |
| total rows | 1701 |
| EX pass rows | 1217 |
| EX fail rows | 484 |
| Eq_acct = 1 | 136 |
| Eq_acct = 0 | 0 |
| Eq_acct = None | 1565 |
| Inv = 1 | 937 |
| Inv = 0 | 92 |
| Inv = None | 672 |
| ASA strict pass | 129 |
| ASA strict fail | 576 |
| ASA strict None | 996 |
| semantic testability count | 129 |
| semantic testability rate among EX-pass | 0.1060 |

## Eq_acct Pipeline Funnel

| Stage | Rows | Pct of EX-pass |
| --- | --- | --- |
| EX-pass rows | 1217 | 1.0000 |
| rows with at least one applicable template | 1056 | 0.8677 |
| rows with at least one fixture state constructed | 1056 | 0.8677 |
| rows with at least one usable support-valid state | 295 | 0.2424 |
| rows with at least one mutant-validated template suite | 136 | 0.1118 |
| rows with generated SQL executed on at least one validated state | 136 | 0.1118 |
| Eq_acct pass rows | 136 | 0.1118 |
| Eq_acct fail rows | 0 | 0.0000 |
| Eq_acct None rows | 1081 | 0.8882 |

## Template-Level Breakdown

| Template | Activated Rows | States Attempted | Usable States | Invalid States | Validated Suites | Generated-Tested States | Eq Failures | Top Invalid Reasons | Top Suite Reasons | Likely Bottleneck |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| posting_side_debit_credit | 591 | 1773 | 0 | 1773 | 0 | 0 | 0 | {'gold_result_empty': 918, 'gold_result_all_null': 855} | {'no_valid_fixture_state': 591} | support validation |
| ar_ap_scope | 0 | 0 | 0 | 0 | 0 | 0 | 0 | {} | {} | not activated |
| income_expense_scope | 373 | 1119 | 0 | 1119 | 0 | 0 | 0 | {'gold_result_empty': 777, 'gold_result_all_null': 342} | {'no_valid_fixture_state': 373} | support validation |
| asset_liability_scope | 0 | 0 | 0 | 0 | 0 | 0 | 0 | {} | {} | not activated |
| balance_count_status_proxy | 295 | 885 | 885 | 0 | 0 | 0 | 0 | {} | {'mutants_not_distinguished': 295} | mutant validation |
| quantity_transaction_count | 339 | 1017 | 885 | 132 | 0 | 0 | 0 | {'gold_result_empty': 102, 'gold_result_all_null': 30} | {'mutant_generation_not_supported': 295, 'no_valid_fixture_state': 44} | mutant validation |
| transaction_type_scope | 476 | 1428 | 885 | 543 | 0 | 0 | 0 | {'gold_result_all_null': 543} | {'mutants_not_distinguished': 295, 'no_valid_fixture_state': 181} | mutant validation |
| customer_vendor_scope | 596 | 1788 | 576 | 1212 | 136 | 408 | 0 | {'gold_result_empty': 621, 'gold_result_all_null': 591} | {'no_valid_fixture_state': 404, 'mutants_not_distinguished': 56} | tested |

## Not-Tested Reason Breakdown

### Row Level

| Reason | Count |
| --- | --- |
| no_valid_fixture_state | 761 |
| gold_result_empty | 466 |
| gold_result_all_null | 295 |
| mutant_generation_not_supported | 295 |
| mutants_not_distinguished | 295 |
| unsupported_sql_feature | 161 |

### Template Level

| Reason | Count |
| --- | --- |
| no_valid_fixture_state | 1593 |
| mutants_not_distinguished | 646 |
| mutant_generation_not_supported | 295 |

### State Level

| Reason | Count |
| --- | --- |
| gold_result_empty | 2418 |
| gold_result_all_null | 2361 |

### Important Reasons

| Reason | Row | Template | State |
| --- | --- | --- | --- |
| no_applicable_template | 0 | 0 | 0 |
| no_valid_fixture_state | 761 | 1593 | 0 |
| gold_sql_execution_error_on_fixture | 0 | 0 | 0 |
| gold_result_empty | 466 | 0 | 2418 |
| gold_result_all_null | 295 | 0 | 2361 |
| gold_literal_not_seeded | 0 | 0 | 0 |
| fixture_schema_invalid | 0 | 0 | 0 |
| accounting_constraint_violation | 0 | 0 | 0 |
| fixture_not_discriminative | 0 | 0 | 0 |
| mutant_generation_not_supported | 295 | 295 | 0 |
| mutants_not_distinguished | 295 | 646 | 0 |
| unsupported_sql_feature | 161 | 0 | 0 |
| timeout | 0 | 0 | 0 |

## Gold Support Failure Analysis

### State Failure Counts

| Reason | Count |
| --- | --- |
| gold_result_empty | 2418 |
| gold_result_all_null | 2361 |

### State Failures By Template

| Template | Reasons |
| --- | --- |
| customer_vendor_scope | {'gold_result_empty': 621, 'gold_result_all_null': 591} |
| income_expense_scope | {'gold_result_empty': 777, 'gold_result_all_null': 342} |
| posting_side_debit_credit | {'gold_result_empty': 918, 'gold_result_all_null': 855} |
| quantity_transaction_count | {'gold_result_empty': 102, 'gold_result_all_null': 30} |
| transaction_type_scope | {'gold_result_all_null': 543} |

### Literal Columns By Failure Reason

| Reason | Literal Columns |
| --- | --- |
| gold_result_all_null | {'customers': 1773, 'transaction_date': 1704, 'transaction_type': 1335, '%Y': 1026, '%m': 1026, 'account_type': 1026, 'vendor': 657, 'start of month': 96, 'product_service': 90, '-7 days': 72, 'weekday 0': 54, '-1 day': 36, '-30 days': 27, '-12 months': 24, '-1 days': 18, '-1 months': 18, 'Donna Hicks': 18, 'Janice Lopez': 18, 'Jennifer Harvey': 18, 'Jeremy Simpson': 18, 'Karen Bowman': 18, 'Meagan Smith': 18, 'Robin Wright': 18, 'Aaron Poole': 9, 'Abigail Potts': 9, 'Alexandra Price': 9, 'Alicia Watkins': 9, 'Alyssa Oconnor': 9, 'Amanda Clayton': 9, 'Amanda Fry': 9, 'Amanda Torres': 9, 'Amy Banks': 9, 'Amy Mendez': 9, 'Andrea Burke': 9, 'Andrew Cain': 9, 'Andrew Spears': 9, 'Angela Herrera': 9, 'Angela Salinas': 9, 'Anna Lewis': 9, 'Annette Alvarado': 9, 'Annette Flores': 9, 'Anthony Crawford': 9, 'Arthur Chapman': 9, 'Arthur Walker': 9, 'Ashley Huff': 9, 'Audrey Green': 9, 'Austin Vazquez': 9, 'Beverly James': 9, 'Bill Mathews': 9, 'Brian Andrews': 9, 'Brian Flowers': 9, 'Bridget Browning': 9, 'Candice James': 9, 'Carla Taylor': 9, 'Carlos Whitney': 9, 'Carmen Bell': 9, 'Carmen Fisher': 9, 'Carol Lewis DVM': 9, 'Casey Parker': 9, 'Catherine Peterson': 9, 'Chad Ware': 9, 'Charles Holmes': 9, 'Charles Ramirez': 9, 'Cheyenne Watson': 9, 'Christian Bishop': 9, 'Christina Carroll': 9, 'Christopher Gomez': 9, 'Christopher Kelly': 9, 'Christopher Vazquez': 9, 'Christopher Young': 9, 'Colin Evans': 9, 'Corey Liu': 9, 'Corey Santos': 9, 'Craig Brady': 9, 'Cynthia Barnett': 9, 'Daisy Bell': 9, 'Dana Sandoval': 9, 'Daniel Coleman': 9, 'Daniel Hill': 9, 'Daniel Nguyen': 9, 'Danielle Caldwell': 9, 'David Lewis': 9, 'David Stone MD': 9, 'Dawn Robinson': 9, 'Deborah Griffin': 9, 'Denise Jennings': 9, 'Dennis Payne': 9, 'Diana James': 9, 'Diana King': 9, 'Donna Jimenez': 9, 'Douglas Barker': 9, 'Douglas Duncan': 9, 'Douglas Reed': 9, 'Edward Carson': 9, 'Edwin Walton': 9, 'Eileen Hunter': 9, 'Eileen Perez': 9, 'Elaine Howard': 9, 'Elizabeth Oliver': 9, 'Elizabeth Sanders': 9, 'Emma Ramirez': 9, 'Eric Brooks': 9, 'Eric Dominguez': 9, 'Erica Fry': 9, 'Erin Solis': 9, 'Francisco Harris': 9, 'Frank Wilkins': 9, 'Gail Schneider': 9, 'Gary Miller': 9, 'Gavin Miller': 9, 'George Smith': 9, 'Hannah Cook': 9, 'Harry Taylor': 9, 'Heather Cox': 9, 'Helen Brown': 9, 'Isaac Gonzalez': 9, 'Jaclyn Stephenson': 9, 'James Best': 9, 'James Carr': 9, 'James Flores': 9, 'James Humphrey': 9, 'Jamie Sanchez': 9, 'Janet Myers': 9, 'Jared Gregory': 9, 'Jared Pena': 9, 'Jared Salazar': 9, 'Jasmine Watson': 9, 'Jason Stewart': 9, 'Javier King': 9, 'Javier Peters': 9, 'Jeffrey Frost': 9, 'Jeffrey Schultz': 9, 'Jennifer Moore PhD': 9, 'Jennifer Odonnell': 9, 'Jennifer Simon': 9, 'Jennifer Wright': 9, 'Jeremy Rice': 9, 'Jessica Baker': 9, 'Jessica Delgado': 9, 'Jessica Pope': 9, 'Jill Walker': 9, 'Joanna King': 9, 'Joel Lawson': 9, 'Johnny Torres': 9, 'Jonathan Galvan PhD': 9, 'Jonathan Moore': 9, 'Jonathan Richard': 9, 'Jonathan Russo': 9, 'Jordan Stone': 9, 'Jose Camacho': 9, 'Joseph Carter': 9, 'Joseph Lucero': 9, 'Joseph Roth': 9, 'Joshua Fry': 9, 'Joshua Ruiz': 9, 'Julie Cook': 9, 'Justin Allison': 9, 'Justin Terry': 9, 'Katelyn Nguyen': 9, 'Kathleen Martinez': 9, 'Kathryn Mcintosh': 9, 'Kathy Phillips': 9, 'Katrina Villarreal': 9, 'Keith Lee': 9, 'Kelly Hamilton': 9, 'Kelly Walker': 9, 'Kendra Gibson': 9, 'Kimberly Barker': 9, 'Kirk Jones': 9, 'Kristen Newman': 9, 'Kristin Young': 9, 'Kristina Blanchard': 9, 'Kyle Bryan': 9, 'Lauren Herrera': 9, 'Lauren Wall': 9, 'Laurie Phelps': 9, 'Leah Mcpherson': 9, 'Leslie Preston DDS': 9, 'Lindsay Young': 9, 'Lisa Morris': 9, 'Lisa Wise': 9, 'Lori Clark': 9, 'Lucas Mitchell': 9, 'Maria Gonzalez': 9, 'Maria Mayo': 9, 'Mark Mora': 9, 'Mark Wright': 9, 'Mary Simpson': 9, 'Mary Williams': 9, 'Melissa Gomez': 9, 'Melissa Johnson': 9, 'Melissa Nelson': 9, 'Michael Baxter': 9, 'Michael Boyd': 9, 'Michael Bryant': 9, 'Michael Crawford': 9, 'Michael Ferguson': 9, 'Michael French': 9, 'Michael Stanley': 9, 'Michael Thomas': 9, 'Michele Glenn': 9, 'Michele Turner': 9, 'Michelle Garcia': 9, 'Mitchell Henderson': 9, 'Mitchell Smith': 9, 'Monique Clark MD': 9, 'Natasha Chung': 9, 'Nichole Alexander': 9, 'Nicole Galloway': 9, 'Omar Logan': 9, 'Omar Rose': 9, 'Pamela Sherman': 9, 'Patrick Carter': 9, 'Patrick Huynh': 9, 'Paul Campbell': 9, 'Paula Johnson': 9, 'Paula Sanchez': 9, 'Phillip Figueroa': 9, 'Phillip Smith': 9, 'Raymond Cole': 9, 'Richard Sexton': 9, 'Robert Gibbs': 9, 'Roberto Peterson': 9, 'Roger Brown': 9, 'Ronald Henderson': 9, 'Sally Gentry': 9, 'Samantha Hatfield': 9, 'Sandra Hayes': 9, 'Sarah Porter': 9, 'Scott Gomez': 9, 'Scott Goodwin': 9, 'Scott Olson': 9, 'Sean Craig': 9, 'Sharon Maynard': 9, 'Sharon Vasquez': 9, 'Sonya Schaefer': 9, 'Stanley Lloyd': 9, 'Stephanie Hooper': 9, 'Stephanie Keller': 9, 'Stephanie Ryan': 9, 'Stephanie Taylor': 9, 'Stephanie Wood': 9, 'Stephen Kim': 9, 'Susan Allen': 9, 'Susan Hensley': 9, 'Sydney Gonzalez': 9, 'Sylvia Bennett': 9, 'Tamara Silva': 9, 'Tanya Long': 9, 'Tanya Mendoza': 9, 'Tara Parker': 9, 'Tara Reid': 9, 'Teresa Cooper': 9, 'Terry Mann': 9, 'Thomas Davidson': 9, 'Thomas Middleton': 9, 'Tiffany Anderson': 9, 'Tiffany Bauer': 9, 'Tiffany Hunter': 9, 'Tim Perez': 9, 'Timothy Flowers': 9, 'Tommy Beard': 9, 'Tony Mcdaniel': 9, 'Travis Boyer': 9, 'Travis Moore': 9, 'Vanessa Rice': 9, 'Vanessa Young': 9, 'Vicki Johnson': 9, 'Vincent George': 9, 'Vincent Hayden': 9, 'Wanda Tate': 9, 'Warren Armstrong': 9, 'Wendy Ford': 9, 'Wesley Cook': 9, 'Wesley Walsh': 9, 'William Jarvis': 9, 'William Perkins': 9, 'Yolanda Simmons': 9, 'Yolanda Williams': 9} |
| gold_result_empty | {'transaction_date': 1854, 'account_type': 1176, 'account': 852, 'product_service': 378, 'start of month': 318, '-7 days': 222, 'customers': 204, 'transaction_type': 204, 'vendor': 186, 'weekday 0': 126, '-30 days': 108, '-12 months': 66, '-1 day': 36, '-1 days': 30, '-1 months': 30, 'Diving': 12, 'Rodney Austin': 12, 'Duplexes': 9, 'Organic Gases': 9, 'Tablets': 9, 'Teen outreach': 9, 'Therapy': 9, 'Aircraft Engines': 6, 'Alan Brown': 6, 'Alyssa Rodriguez': 6, 'Amanda Miller': 6, 'Andrew Ryan': 6, 'Andrew Vargas': 6, 'Anthony Harris': 6, 'Antonio Duran': 6, 'Barbara Watson': 6, 'Boats': 6, 'Brandon Marshall': 6, 'Brent Rodriguez': 6, 'Brett Banks': 6, 'Brian Hurley': 6, 'Brittany Brown': 6, 'Business management': 6, 'Carla Dunn': 6, 'Carla Whitaker': 6, 'Casey Parker': 6, 'Cassandra Willis': 6, 'Chase Jenkins': 6, 'Chelsea Lee': 6, 'Construction Projects': 6, 'Contracts': 6, 'Corey Sampson': 6, 'Crystal Lane': 6, 'Daily living assistance': 6, 'Daisy Cruz': 6, 'Daniel Zavala': 6, 'Darryl Castillo': 6, 'Data processing computer': 6, 'David Park': 6, 'Diapers': 6, 'Don Moore': 6, 'Eric Mcpherson': 6, 'Eric Merritt': 6, 'Fast food': 6, 'Fishing': 6, 'Hacking Training': 6, 'Hannah Lyons': 6, 'Helium': 6, 'Holly Jones': 6, 'Jean Rose': 6, 'Jeffrey Powell PhD': 6, 'Jennifer Wright': 6, 'Johnathan Payne': 6, 'Joseph Moreno': 6, 'Justin Cowan': 6, 'Kelly Lee': 6, 'Laura Montes': 6, 'Lauren Martin': 6, 'Lemon oil': 6, 'Mailbox Rental': 6, 'Marine Supplies': 6, 'Mark Banks': 6, 'Martha Conner': 6, 'Matthew Serrano': 6, 'Michelle Bridges': 6, 'Michelle Williams': 6, 'Monica Morrison': 6, 'Natalie Medina': 6, 'Nicholas Perez': 6, 'Nicole Leblanc': 6, 'Nursing and health': 6, 'Outdoor goods': 6, 'PCs': 6, 'Patricia Shannon': 6, 'Raymond Conrad': 6, 'Richard Foster': 6, 'Samuel Winters': 6, 'Sandra Ross': 6, 'Sarah Williams': 6, 'Scott Lopez': 6, 'Sean Underwood': 6, 'Service Provider': 6, 'Ships': 6, 'Snow removal services': 6, 'Tanner Padilla': 6, 'Tara Pearson': 6, 'Tracy Carr': 6, 'Ventilation cleaning': 6, 'Veronica Harris': 6, 'Vicki Wilson DDS': 6, 'Victoria Stewart': 6, 'William Mcfarland': 6, 'Abandoned Infant': 3, 'Adoption': 3, 'Aircraft': 3, 'Assessing product problems': 3, 'Barge': 3, 'Biomass electricity': 3, 'Brokerage': 3, 'Car seats': 3, 'Coffee and Snack': 3, 'Computer Programming': 3, 'Construction management': 3, 'Contract': 3, 'Copying': 3, 'Distressed Securities': 3, 'Duct and gutter cleaning': 3, 'Environmental impact assessment': 3, 'Equity long bias': 3, 'Eucalyptus oil': 3, 'Event-driven': 3, 'Financial Planning': 3, 'Fluorocarbon gases': 3, 'Food & drink': 3, 'Foster Care': 3, 'Golf Courses': 3, 'Heavy construction equipment': 3, 'Homemaker and companion': 3, 'Housing': 3, 'Industrial manufacturing': 3, 'Inorganic Gases': 3, 'Intravenous Therapy': 3, 'Investment': 3, 'Leveling and Trimming': 3, 'Macs': 3, 'Mining machinery': 3, 'Miscellaneous': 3, 'Mobile Device': 3, 'Musicians': 3, 'Non-nuclear': 3, 'Non-renewable': 3, 'Opera': 3, 'Orange oil': 3, 'Parking lot and driveway washing': 3, 'Pool maintenance': 3, 'Postal': 3, 'Printing': 3, 'Railroad Cars': 3, 'Railroad Equipment': 3, 'Recreational programs': 3, 'Self-help programs': 3, 'Senior-care': 3, 'Service Provisioning': 3, 'Services': 3, 'Shearing': 3, 'Shunting trailers': 3, 'Spraying': 3, 'Surveying wells': 3, 'Therapeutic': 3, 'Tournaments and Matches': 3, 'Training': 3, 'Transportation': 3, 'Wardrobe': 3, 'Waste-fueled': 3, 'Wind power': 3} |

### Date Predicate Rows By Failure Reason

| Reason | Count |
| --- | --- |
| gold_result_empty | 1854 |
| gold_result_all_null | 1704 |

### Entity Literal Rows By Failure Reason

| Reason | Entity Literal Flags |
| --- | --- |
| gold_result_all_null | {'has_customer_literal': 1773, 'has_account_literal': 1026, 'has_vendor_literal': 657, 'has_product_literal': 90} |
| gold_result_empty | {'has_account_literal': 2028, 'has_product_literal': 378, 'has_customer_literal': 204, 'has_vendor_literal': 186} |

## Mutant Validation Failure Analysis

### Suite Failure Reasons By Template

| Template | Reasons |
| --- | --- |
| balance_count_status_proxy | {'mutants_not_distinguished': 295} |
| customer_vendor_scope | {'no_valid_fixture_state': 404, 'mutants_not_distinguished': 56} |
| income_expense_scope | {'no_valid_fixture_state': 373} |
| posting_side_debit_credit | {'no_valid_fixture_state': 591} |
| quantity_transaction_count | {'mutant_generation_not_supported': 295, 'no_valid_fixture_state': 44} |
| transaction_type_scope | {'mutants_not_distinguished': 295, 'no_valid_fixture_state': 181} |

### Mutant Family Generation

| Family | Generated/Skipped |
| --- | --- |
| count_rows_to_distinct_transactions | {'no_safe_rewrite_or_no_change': 295} |
| remove_party_predicate | {'generated': 192} |
| remove_transaction_type_predicate | {'generated': 295} |
| replace_balance_with_count | {'no_safe_rewrite_or_no_change': 295} |
| replace_open_balance_with_amount | {'generated': 295} |
| replace_quantity_with_count | {'no_safe_rewrite_or_no_change': 295} |
| swap_customer_vendor_columns | {'generated': 192} |
| swap_transaction_type_literals | {'generated': 295} |

### Mutant Execution Status

| Template | Execution Status |
| --- | --- |
| balance_count_status_proxy | {'equal': 885} |
| customer_vendor_scope | {'equal': 744, 'distinguished': 136} |
| transaction_type_scope | {'equal': 1770} |

## Activated-But-Never-Tested Templates

| Template | Activated Rows | Likely Bottleneck |
| --- | --- | --- |
| posting_side_debit_credit | 591 | support validation |
| income_expense_scope | 373 | support validation |
| balance_count_status_proxy | 295 | mutant validation |
| quantity_transaction_count | 339 | mutant validation |
| transaction_type_scope | 476 | mutant validation |

## Representative Row Examples


### activated_but_none_validate

- `booksql_070842` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Appearances and speeches") and transaction_date BETWEEN date( c...`
- `booksql_070850` templates=['posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 6}. gold=`select min(credit) from master_txn_table where transaction_type = 'invoice' and instr(account,"Manufacturing industrial organic gases") and transaction_date BETWEEN date( curren...`
- `booksql_070859` templates=['customer_vendor_scope', 'posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select avg(credit) from master_txn_table where customers = "Amanda Fry" and transaction_type = 'bill' and vendor = "Robin Wright"`
- `booksql_070869` templates=['customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(quantity) from master_txn_table where customers = "Carmen Fisher" and product_service = 'Eucalyptus oil' and transaction_type in ('invoice', 'sales receipt') and tran...`
- `booksql_070887` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Christina Carroll" and instr(account,"Accounts R...`
- `booksql_070888` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Jose Camacho" and instr(account,"Arizona Dept. o...`
- `booksql_070895` templates=['customer_vendor_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select date(transaction_date, 'start of month'), sum(debit) from master_txn_table where vendor = "Carla Whitaker" group by date(transaction_date, 'start of month')`
- `booksql_070899` templates=['income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select account, sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Expense','Other Expense') and transac...`
- `booksql_070912` templates=['customer_vendor_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select customers, sum(credit) from master_txn_table where account in ('Income','Other Income') and transaction_date BETWEEN date(current_date, 'start of year') AND date(current_...`
- `booksql_070915` templates=['customer_vendor_scope', 'quantity_transaction_count'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select transaction_id from master_txn_table where transaction_type = 'payment' and customers = "Don Moore" and transaction_date BETWEEN date(current_date, 'start of year','+3 mo...`

### customer_vendor_validates

- `booksql_070863` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "David Meza" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070913` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Matthew Hunter" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070950` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Trevor Harvey" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN d...`
- `booksql_071041` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Jake Sanford" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`
- `booksql_071052` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Shawn Taylor" and transaction_type = 'invoice' and open_balance>0`
- `booksql_071066` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Ryan Joseph" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN dat...`
- `booksql_071180` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Chad Rodriguez" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN ...`
- `booksql_071214` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "David Hurley" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`
- `booksql_071233` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Kristi Gibson" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN d...`
- `booksql_071275` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Cheryl Bowen" and transaction_type = 'invoice' and open_balance>0`

### gold_result_all_null

- `booksql_070850` templates=['posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 6}. gold=`select min(credit) from master_txn_table where transaction_type = 'invoice' and instr(account,"Manufacturing industrial organic gases") and transaction_date BETWEEN date( curren...`
- `booksql_070859` templates=['customer_vendor_scope', 'posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select avg(credit) from master_txn_table where customers = "Amanda Fry" and transaction_type = 'bill' and vendor = "Robin Wright"`
- `booksql_070869` templates=['customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(quantity) from master_txn_table where customers = "Carmen Fisher" and product_service = 'Eucalyptus oil' and transaction_type in ('invoice', 'sales receipt') and tran...`
- `booksql_070887` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Christina Carroll" and instr(account,"Accounts R...`
- `booksql_070888` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Jose Camacho" and instr(account,"Arizona Dept. o...`
- `booksql_070923` templates=['posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 6}. gold=`select max(credit) from master_txn_table where transaction_type = 'invoice' and instr(account,"golf equipment rental services") and transaction_date BETWEEN date(current_date) A...`
- `booksql_070930` templates=['customer_vendor_scope', 'posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select avg(credit) from master_txn_table where customers = "Frank Wilkins" and transaction_type = 'bill' and vendor = "Jennifer Harvey"`
- `booksql_070967` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Amanda Clayton" and instr(account,"Uncategorized...`
- `booksql_070975` templates=['customer_vendor_scope', 'income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_all_null': 9, 'no_valid_fixture_state': 3} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 9}. gold=`select sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where T1.customers = "Tara Reid" and instr(account,"Labor") and strfti...`
- `booksql_071023` templates=['posting_side_debit_credit', 'transaction_type_scope'] reasons={'gold_result_all_null': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_all_null': 6}. gold=`select max(credit) from master_txn_table where transaction_type = 'invoice' and instr(account,"Bars and nightclubs") and transaction_date BETWEEN date(current_date, '-1 year', '...`

### gold_result_empty

- `booksql_070895` templates=['customer_vendor_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select date(transaction_date, 'start of month'), sum(debit) from master_txn_table where vendor = "Carla Whitaker" group by date(transaction_date, 'start of month')`
- `booksql_070899` templates=['income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select account, sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Expense','Other Expense') and transac...`
- `booksql_070912` templates=['customer_vendor_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select customers, sum(credit) from master_txn_table where account in ('Income','Other Income') and transaction_date BETWEEN date(current_date, 'start of year') AND date(current_...`
- `booksql_070915` templates=['customer_vendor_scope', 'quantity_transaction_count'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select transaction_id from master_txn_table where transaction_type = 'payment' and customers = "Don Moore" and transaction_date BETWEEN date(current_date, 'start of year','+3 mo...`
- `booksql_070918` templates=['income_expense_scope'] reasons={'gold_result_empty': 3, 'no_valid_fixture_state': 1} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 3}. gold=`select transaction_date from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Income','Other Income') and product_serv...`
- `booksql_070920` templates=['income_expense_scope'] reasons={'gold_result_empty': 3, 'no_valid_fixture_state': 1} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 3}. gold=`select transaction_date from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Income','Other Income') and product_serv...`
- `booksql_070941` templates=['income_expense_scope'] reasons={'gold_result_empty': 3, 'no_valid_fixture_state': 1} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 3}. gold=`select transaction_date from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Income','Other Income') and product_serv...`
- `booksql_070945` templates=['income_expense_scope'] reasons={'gold_result_empty': 3, 'no_valid_fixture_state': 1} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 3}. gold=`select transaction_date from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Income','Other Income') and product_serv...`
- `booksql_070951` templates=['income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select account, sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Expense','Other Expense') and transac...`
- `booksql_070979` templates=['income_expense_scope', 'posting_side_debit_credit'] reasons={'gold_result_empty': 6, 'no_valid_fixture_state': 2} summary=Fixture states were built but none were support-valid; top state reasons: {'gold_result_empty': 6}. gold=`select account, sum(debit) from master_txn_table as T1 join chart_of_accounts as T2 on T1.account = T2.account_name where account_type in ('Expense','Other Expense') and transac...`

### mutant_generation_not_supported

- `booksql_070842` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Appearances and speeches") and transaction_date BETWEEN date( c...`
- `booksql_070863` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "David Meza" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070913` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Matthew Hunter" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070935` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Lorraine Valdez" and transaction_type = 'invoice' and open_balance >0 and transaction_date < strft...`
- `booksql_070936` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Retailing formal dining furniture") and transaction_date BETWEE...`
- `booksql_070944` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Molly Morton" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`
- `booksql_070950` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Trevor Harvey" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN d...`
- `booksql_071005` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Reginald Ramos" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN ...`
- `booksql_071011` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Constructing bangalow") and transaction_date BETWEEN date(curre...`
- `booksql_071041` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Jake Sanford" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`

### mutants_not_distinguished

- `booksql_070842` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Appearances and speeches") and transaction_date BETWEEN date( c...`
- `booksql_070863` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "David Meza" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070913` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Matthew Hunter" and transaction_type = 'invoice' and open_balance>0`
- `booksql_070935` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Lorraine Valdez" and transaction_type = 'invoice' and open_balance >0 and transaction_date < strft...`
- `booksql_070936` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Retailing formal dining furniture") and transaction_date BETWEE...`
- `booksql_070944` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Molly Morton" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`
- `booksql_070950` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Trevor Harvey" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN d...`
- `booksql_071005` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 3} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 3, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Reginald Ramos" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN ...`
- `booksql_071011` templates=['balance_count_status_proxy', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {'mutants_not_distinguished': 2, 'mutant_generation_not_supported': 1}. gold=`select count(distinct transaction_id) from master_txn_table where transaction_type = 'invoice' and instr(account,"Constructing bangalow") and transaction_date BETWEEN date(curre...`
- `booksql_071041` templates=['balance_count_status_proxy', 'customer_vendor_scope', 'quantity_transaction_count', 'transaction_type_scope'] reasons={'mutant_generation_not_supported': 1, 'mutants_not_distinguished': 2} summary=Eq_acct decided after testing 3 validated states. gold=`select count(distinct transaction_id) from master_txn_table where customers = "Jake Sanford" and transaction_type = 'invoice' and open_balance >0 and transaction_date BETWEEN da...`

### unsupported_sql_feature

- `booksql_070829` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_070865` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_070872` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_070938` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_070976` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_071111` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_071139` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_071216` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_071231` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`
- `booksql_071299` templates=[] reasons={'unsupported_sql_feature': 1} summary=No accounting template activated from gold SQL/schema evidence. gold=`SELECT avg(quantity_per_transaction) FROM (select transaction_id, sum(quantity) as quantity_per_transaction from master_txn_table where transaction_type = 'sales receipt' and pa...`

## Recommended Fix Order

- 1. Add debug-only parsing lineage categories for unsupported SQL before broadening coverage; unsupported lineage currently affects 161 rows.
- 2. Improve gold-support fixture seeding for activated templates, especially date/literal/account support, because empty or all-null gold results dominate state-level support failures.
- 3. Expand mutant debug coverage and AST rewrite support for activated templates where no safe mutant is generated; keep generation gold-only.
- 4. Calibrate fixture states against existing mutant families so usable states actually distinguish at least one pre-specified mutant at suite level.
- 5. Only after the above, consider activation changes; do not broaden activation until fixture and mutant bottlenecks are explained.
