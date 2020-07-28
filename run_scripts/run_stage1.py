"""python 3.6+
Run MASH screen and parse output
Carmen Sheppard 2019-2020
"""

import os
import sys
from run_scripts.initialise_run import Category
from run_scripts.tools import apply_filters, create_csv, create_dataframe
from Database_tools.db_functions import session_maker
from Database_tools.sqlalchemydeclarative import Serotype, Group


def get_pheno_list(serotype_hits, session):
    """
    Function to return phenotype list from list of serotype hits (deduplicated)
    :param serotype_hits: list of serotype hits from stage 1 mash analysis
    :param session: DB session
    :return: list of deduplicated phenotypes
    """
    out_res = []

    for hit in serotype_hits:
        # get back
        grp = session.query(Serotype).join(Group).filter(Serotype.serotype_hit == hit).all()
        # if hit is in group get group name
        if grp:
            for g in grp:
                out_res.append(g.genogroup.group_name)
        # if hit is type get phenotype name
        else:
            pheno = session.query(Serotype.predicted_pheno).filter(Serotype.serotype_hit == hit) \
                .all()
            out_res.append(pheno[0][0])
    out_res = set(out_res)
    return out_res


def group_check(df, database):
    """
    Defined groups for subtyping, report singletons, mixed and failed.
    :param df: filtered dataframe from mash screen
    :param database: path to ctvdb
    :return:strings of group and results
    """
    folder = None
    grp_id = None
    results = []

    # collate the data results together on one line and create result list
    for index, rows in df.iterrows():
        result = rows.Serotype
        results.append(result)


    # create db session
    session = session_maker(database)

    # initialise empty list for group ids and group info
    groups = []
    grp_info = []

    #initialise empty list for types
    types = []
    # go through query results and append to groups list if not "no results".
    for i in results:
        genogroup = session.query(Serotype).join(Group).filter(Serotype.serotype_hit == i).all()
        if genogroup:
            groups.append(genogroup[0].group_id)
            grp_info.append(genogroup[0])
        else: # if not in a group add the type that was found
            types.append(i)

    # check length of set group is > 1 then not all group's were identical, hence Mixed. Or there are
    # mixed groups and types
    if len(set(groups)) > 1 or groups and types:
        # get phenotype info for hits to add to result output
        pheno = set(get_pheno_list(results, session))
        session.close()
        # deal with subtypes in stage 1
        if len(pheno) > 1:
            category = Category.mix
            stage1_result = f"Mixed serotypes- {pheno}"
            sys.stdout.write(f"Mixed serotypes found - {pheno}\n")
        else:
            category = Category.subtype
            stage1_result = pheno

    # if only one hit and that hit is not in a group
    elif not groups and len(results) == 1:
        # get the phenotype from the db using function
        stage1_result = list(get_pheno_list(results, session))
        # get element for display
        stage1_result = stage1_result[0]
        # if stage 1 hit not found raise error
        if not stage1_result:
            sys.stderr.write(f"Stage 1 hit unexpected - please check "
                             f"integrity of CTVdb, all reference sequences MUST"
                             f" be accounted for in CTVdb.\n")
            sys.exit(1)
        session.close()
        category = Category.type

    # if more than one hit but they are all types with no groups or SUBTYPES
    elif not groups and len(results) > 1:
        # get phenotypes for output
        pheno = set(get_pheno_list(results, session))
        if len(pheno) > 1:
            category = Category.mix
            stage1_result = f"Mixed serotypes- {pheno}"
            sys.stdout.write(f"Mixed serotypes found - {pheno}\n")
            session.close()
        else:
            category = Category.subtype
            stage1_result = list(pheno)[0]
            sys.stdout.write(f"Mixed serotypes found - {pheno}\n")

    # if not meeting above criteria must be a group (even if only 1 hit)
    else:
        # retrieve group_name and group ID
        if grp_info:
            for record in set(grp_info):
                folder = record.genogroup.group_name
                grp_id = record.group_id
            category = Category.variants
            stage1_result = folder
            session.close()

        else:
            sys.stderr.write(f"Stage 1 group unexpected - please check "
                             f"integrity of CTVdb, all groups MUST"
                             f" be accounted for in CTVdb.\n")
            sys.exit(1)

    return category, stage1_result, folder, grp_id


def run_parse(analysis, tsvfile):
    # Run stage 1 MASH screen file parsing
    # check tsv file not empty

    try:
        # check file is not empty then open and create df
        if os.path.isfile(tsvfile) and os.path.getsize(tsvfile) > 0:
            df = create_dataframe(tsvfile)
            filename = os.path.basename(tsvfile)[:-4]
            alldata = f'{filename}.csv'

            # Apply filters
            filtered_df, original, analysis.top_hits = \
                apply_filters(df, analysis.minpercent, analysis.minmulti)
            analysis.max_percent = round(original['percent'].max(),2)

            if not filtered_df.empty:
                # sort dataframes by percent then identity descending.
                filtered_df = filtered_df.sort_values(by=["percent",
                                                          "identity"],
                                                      ascending=False)
                analysis.category, analysis.stage1_result, analysis.folder, analysis.grp_id = \
                    group_check(filtered_df, analysis.database)
                if analysis.category == Category.mix:
                    analysis.rag_status = "AMBER"
                else:
                    analysis.rag_status = "GREEN"

            else:  # for samples with no hits

                # second chance, amber rag status for low top hits
                if analysis.max_percent >= 70 and analysis.minpercent >= 70:
                    analysis.rag_status = "AMBER"
                    # reduce minpercent cut off to: max percentage - 10%
                    minpercent = analysis.max_percent - (analysis.max_percent*0.1)
                    # rerun filter
                    filtered_df, original, analysis.top_hits = apply_filters(df,
                                                minpercent, analysis.minmulti)
                    analysis.category, analysis.stage1_result, analysis.folder, analysis.grp_id\
                        = group_check(filtered_df, analysis.database)

                elif analysis.max_percent < 20:
                    analysis.category = Category.acapsular
                    analysis.stage1_result = "Below 20% hit - possible acapsular"\
                                             " organism, check species identity and sequence quality."

                else:
                    analysis.category = Category.no_hits
                    analysis.stage1_result = "Below 70% hit - Poor Sequence " \
                                             "quality, variant or non-typeable"\
                                             " organism."

                sys.stdout.write(analysis.stage1_result + "\n")

            original = original.sort_values(by=["percent", "identity"],
                                            ascending=False)

            create_csv(original, analysis.output_dir, alldata)


        else:
            sys.stderr.write("ERROR: No Mash data - empty file\n")
            sys.exit(1)

    except IOError:
        # warning about weird file path
        sys.stderr.write("ERROR: Mash output path not available.\n")
        sys.exit(1)

