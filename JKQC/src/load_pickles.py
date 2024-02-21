def load_pickles(input_pkl,Qout):
  from pandas import DataFrame
  if len(input_pkl) == 0:
    clusters_df = DataFrame()
  else:
    from pandas import read_pickle
    for i in range(len(input_pkl)):
      newclusters_df = read_pickle(input_pkl[i])
      if not isinstance(newclusters_df, DataFrame):
        print("File "+input_pkl[i]+" is not JKQC-compatible Pandas.DataFrame. Try to use JKTS.")
        exit()
      if Qout >= 1 and len(input_pkl) > 1:
        print("Number of files in "+input_pkl[i]+": "+str(len(newclusters_df)))
      if i == 0:
        clusters_df = newclusters_df
      else:
        clusters_df = clusters_df.append(newclusters_df, ignore_index=True)
    clusters_df = clusters_df.reset_index(drop=True)
  return clusters_df
