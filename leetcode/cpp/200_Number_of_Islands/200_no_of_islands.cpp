
class Solution {
public:
    int numIslands(vector<vector<char>>& grid) {
        int islands = 0;
        int rows = grid.size();
        int cols = grid[0].size();
        vector<vector<int>> visited(rows, vector<int>(cols,0));

        for (int i =0; i< rows; i++){
            for (int j =0; j< cols; j++){
                if (grid[i][j]=='1' && visited[i][j]!=-1){
                    islands++;
                    bfs(i,j,rows,cols,grid,visited);
                }
            }
        }        
        return islands;
    }

    void bfs(int r,int c, int rows, int cols,vector<vector<char>>& grid ,vector<vector<int>>& visited){
        queue<pair<int,int>> q;
        q.push({r,c});
        while (!q.empty()){
            r = q.front().first;
            c = q.front().second;
            cout << r << " " << c << endl;
            q.pop();
            if (r >=0 && c >=0 && c < cols && r < rows && grid[r][c]=='1' && visited[r][c] != -1){
                visited[r][c] = -1;
                q.push({r+1,c});
                q.push({r-1,c});
                q.push({r,c+1});
                q.push({r,c-1});
            }

        } 
    }
};